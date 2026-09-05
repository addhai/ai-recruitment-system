"""工作流节点的数据库副作用层。

所有 LangGraph 节点对业务表的真实写入都收敛到这里，统一保证：
1. 幂等：工作流恢复重入时不会产生重复业务数据（先查后写）；
2. 短事务：每个动作独立 SessionLocal，节点间互不持有长事务；
3. 可降级：纯 SQLAlchemy 操作，不依赖 LLM。
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.models.database import (
    SessionLocal,
    Candidate,
    Resume,
    Interview,
    Questionnaire,
    QuestionnaireResponse,
    Evaluation,
    TalentPool,
    User,
)


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------- 候选人/简历

def get_candidate(candidate_id: int) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
        if not c:
            return None
        return {
            "id": c.id,
            "name": c.name,
            "position": c.position,
            "status": c.status,
            "resume_text": c.resume_text,
        }


def set_candidate_status(candidate_id: int, status: str) -> None:
    """驱动候选人状态机：screening / questionnaire / interviewing / hired / rejected / talent_pool"""
    with session_scope() as db:
        c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
        if c and c.status != status:
            c.status = status


def get_resume_parsed(candidate_id: int) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        r = db.query(Resume).filter(Resume.candidate_id == candidate_id).first()
        return r.parsed_data if r else None


def save_resume_parsed(
    candidate_id: int,
    parsed_data: Dict[str, Any],
    skills: Any = None,
    experience: Optional[str] = None,
    education: Optional[str] = None,
) -> None:
    """简历结构化解析结果落库（幂等：已有 Resume 则更新）"""
    with session_scope() as db:
        resume = db.query(Resume).filter(Resume.candidate_id == candidate_id).first()
        if resume:
            resume.parsed_data = parsed_data
            if skills is not None:
                resume.skills = skills
            if experience:
                resume.experience = experience
            if education:
                resume.education = education
        else:
            db.add(Resume(
                candidate_id=candidate_id,
                file_name="AI解析简历",
                file_path=f"/resumes/{candidate_id}",
                parsed_data=parsed_data,
                skills=skills if isinstance(skills, list) else None,
                experience=experience,
                education=education,
            ))


# ---------------------------------------------------------------- 评估维度

def upsert_evaluation(candidate_id: int, dimension: str, score: float, comment: str = "") -> None:
    """维度评估结果落库（幂等：同候选人同维度更新分数）"""
    score_int = int(max(0, min(100, round(score))))
    with session_scope() as db:
        ev = (
            db.query(Evaluation)
            .filter(Evaluation.candidate_id == candidate_id, Evaluation.dimension == dimension)
            .first()
        )
        if ev:
            ev.score = score_int
            ev.comment = comment
        else:
            db.add(Evaluation(
                candidate_id=candidate_id,
                evaluator_id=None,  # AI 评估，无真人评估人
                dimension=dimension,
                score=score_int,
                comment=comment,
            ))


# ---------------------------------------------------------------- 问卷

def create_questionnaire(candidate_id: int, name: str, qtype: str, questions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """AI 生成问卷落库（幂等：按 name 复用，工作流 resume 重放时不重复建卷）"""
    with session_scope() as db:
        existing = db.query(Questionnaire).filter(Questionnaire.name == name).first()
        if existing:
            return {"questionnaire_id": existing.id, "created": False}
        q = Questionnaire(
            name=name,
            type=qtype,
            questions=questions,
            created_by=None,  # AI 生成
        )
        db.add(q)
        db.flush()
        return {"questionnaire_id": q.id, "created": True}


def save_questionnaire_response(
    candidate_id: int,
    questionnaire_id: int,
    responses: Dict[str, Any],
    score: Optional[int] = None,
) -> int:
    """问卷作答记录落库（幂等：同候选人同问卷更新），返回 response_id"""
    with session_scope() as db:
        resp = (
            db.query(QuestionnaireResponse)
            .filter(
                QuestionnaireResponse.candidate_id == candidate_id,
                QuestionnaireResponse.questionnaire_id == questionnaire_id,
            )
            .first()
        )
        if resp:
            resp.responses = responses
            if score is not None:
                resp.score = score
            return resp.id
        resp = QuestionnaireResponse(
            candidate_id=candidate_id,
            questionnaire_id=questionnaire_id,
            responses=responses,
            score=score,
            status="completed",
        )
        db.add(resp)
        db.flush()
        return resp.id


# ---------------------------------------------------------------- 面试

def _pick_interviewer(db, seed: int) -> Optional[int]:
    """从有面试官角色的用户中按种子轮询分配（简单确定性负载均衡）"""
    interviewers = (
        db.query(User)
        .filter(User.role.in_(["interviewer", "admin", "hr"]))
        .all()
    )
    if not interviewers:
        return None
    return interviewers[seed % len(interviewers)].id


def _next_workday_10am(days_ahead: int = 3) -> datetime:
    """简单排期规则：N 天后的上午 10 点，遇周末顺延到周一"""
    day = datetime.utcnow() + timedelta(days=days_ahead)
    day = day.replace(hour=10, minute=0, second=0, microsecond=0)
    while day.weekday() >= 5:  # 5=周六 6=周日
        day += timedelta(days=1)
    return day


def schedule_interview(candidate_id: int, round_no: int, position: Optional[str]) -> Dict[str, Any]:
    """自动排期面试：分配面试官 + 时间，写 Interview 表（幂等：同候选人同轮次复用）"""
    with session_scope() as db:
        existing = (
            db.query(Interview)
            .filter(Interview.candidate_id == candidate_id, Interview.round == round_no)
            .first()
        )
        if existing:
            return {
                "interview_id": existing.id,
                "round": existing.round,
                "interviewer_id": existing.interviewer_id,
                "scheduled_at": existing.scheduled_at.isoformat() if existing.scheduled_at else None,
                "created": False,
            }

        interviewer_id = _pick_interviewer(db, seed=candidate_id + round_no)
        scheduled_at = _next_workday_10am(days_ahead=2 + round_no)
        iv = Interview(
            candidate_id=candidate_id,
            position=position or "待定岗位",
            round=round_no,
            status="scheduled",
            interviewer_id=interviewer_id,
            scheduled_at=scheduled_at,
        )
        db.add(iv)
        db.flush()
        return {
            "interview_id": iv.id,
            "round": iv.round,
            "interviewer_id": interviewer_id,
            "scheduled_at": scheduled_at.isoformat(),
            "created": True,
        }


def update_interview_questions(interview_id: int, questions: List[Dict[str, Any]]) -> None:
    """写入 AI 生成的面试题（幂等：已存在题目则不覆盖，保证 replay 安全）"""
    with session_scope() as db:
        iv = db.query(Interview).filter(Interview.id == interview_id).first()
        if not iv:
            return
        if iv.questions:  # 已有题目不覆盖
            return
        iv.questions = questions


def complete_interview_record(
    interview_id: int,
    score: int,
    feedback: str = "",
    notes: Optional[str] = None,
) -> None:
    """面试结果回填（人工录入的真实分数）"""
    with session_scope() as db:
        iv = db.query(Interview).filter(Interview.id == interview_id).first()
        if not iv:
            return
        iv.status = "completed"
        iv.score = int(max(0, min(100, score)))
        iv.feedback = feedback
        if notes:
            iv.notes = notes
        iv.completed_at = datetime.utcnow()


def get_interview(candidate_id: int, round_no: int) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        iv = (
            db.query(Interview)
            .filter(Interview.candidate_id == candidate_id, Interview.round == round_no)
            .first()
        )
        if not iv:
            return None
        return {
            "interview_id": iv.id,
            "round": iv.round,
            "status": iv.status,
            "score": iv.score,
            "questions": iv.questions,
        }


# ---------------------------------------------------------------- 人才池

def upsert_talent_pool(candidate_id: int, tags: List[str], notes: str) -> None:
    """人才池沉淀（幂等：已在池中则更新标签与备注）"""
    with session_scope() as db:
        tp = db.query(TalentPool).filter(TalentPool.candidate_id == candidate_id).first()
        if tp:
            merged = list(dict.fromkeys((tp.tags or []) + tags))
            tp.tags = merged
            tp.notes = notes
            tp.status = "active"
        else:
            db.add(TalentPool(
                candidate_id=candidate_id,
                status="active",
                tags=tags,
                notes=notes,
                last_contact=datetime.utcnow(),
            ))
