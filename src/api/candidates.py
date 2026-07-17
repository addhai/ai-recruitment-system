import os
import time
import tempfile
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Body, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional
from src.models.database import get_db, Candidate, Resume, WorkflowRun
from src.models.schemas import CandidateCreate, CandidateUpdate, CandidateResponse, ResumeCreate, ResumeResponse
from src.api.auth import get_current_user
from src.safety import InputGuard, OutputGuard
from src.evaluation import evaluation_tracker
from src.sse.notification import (
    notify_candidate_added,
    notify_workflow_progress,
    notify_hiring_decision,
)
from src.services.feishu_notify import notify_workflow_completed_async


def _sanitize_output(obj):
    """递归过滤输出对象中的字符串字段，移除PII信息"""
    if isinstance(obj, str):
        return OutputGuard.sanitize(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_output(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_output(item) for item in obj]
    return obj

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.get("/", response_model=List[CandidateResponse])
def list_candidates(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    position: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    query = db.query(Candidate)
    
    if status:
        query = query.filter(Candidate.status == status)
    if position:
        query = query.filter(Candidate.position == position)
    if search:
        query = query.filter(
            Candidate.name.contains(search) |
            Candidate.email.contains(search)
        )
    
    return query.offset(skip).limit(limit).all()


@router.get("/{candidate_id}", response_model=CandidateResponse)
def get_candidate(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@router.post("/", response_model=CandidateResponse)
async def create_candidate(candidate: CandidateCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if candidate.email:
        existing = db.query(Candidate).filter(Candidate.email == candidate.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already exists")
    
    new_candidate = Candidate(
        name=candidate.name,
        email=candidate.email,
        phone=candidate.phone,
        source=candidate.source,
        position=candidate.position
    )
    db.add(new_candidate)
    db.commit()
    db.refresh(new_candidate)

    # 通过 SSE 通知前端有新候选人加入
    try:
        await notify_candidate_added(new_candidate.id, new_candidate.name)
    except Exception:
        pass
    
    return new_candidate


@router.put("/{candidate_id}", response_model=CandidateResponse)
def update_candidate(candidate_id: int, candidate: CandidateUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    db_candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not db_candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    if candidate.name:
        db_candidate.name = candidate.name
    if candidate.email:
        db_candidate.email = candidate.email
    if candidate.phone:
        db_candidate.phone = candidate.phone
    if candidate.status:
        db_candidate.status = candidate.status
    if candidate.source:
        db_candidate.source = candidate.source
    if candidate.position:
        db_candidate.position = candidate.position
    
    db.commit()
    db.refresh(db_candidate)
    return db_candidate


@router.delete("/{candidate_id}")
def delete_candidate(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    db.delete(candidate)
    db.commit()
    return {"message": "Candidate deleted"}


@router.post("/{candidate_id}/resume", response_model=ResumeResponse)
def upload_resume(candidate_id: int, resume: ResumeCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    new_resume = Resume(
        candidate_id=candidate_id,
        file_name=resume.file_name,
        file_path=resume.file_path,
        parsed_data=resume.parsed_data,
        skills=resume.skills,
        experience=resume.experience,
        education=resume.education
    )
    db.add(new_resume)
    db.commit()
    db.refresh(new_resume)
    return new_resume


@router.get("/{candidate_id}/resume", response_model=ResumeResponse)
def get_resume(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    resume = db.query(Resume).filter(Resume.candidate_id == candidate_id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume


def _extract_text_from_file(file_path: str, filename: str) -> str:
    """根据文件类型提取文本内容，支持 .txt/.pdf/.docx"""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".txt":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    if ext == ".pdf":
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            texts = []
            for page in reader.pages:
                text = page.extract_text() or ""
                texts.append(text)
            return "\n".join(texts).strip()
        except ImportError:
            # 没有安装 PyPDF2 时，回退使用文件名
            return f"[PyPDF2 未安装，无法解析 PDF：{filename}]"
        except Exception as e:
            return f"[PDF 解析失败：{e}]"

    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
        except ImportError:
            # 没有安装 python-docx 时，回退使用文件名
            return f"[python-docx 未安装，无法解析 DOCX：{filename}]"
        except Exception as e:
            return f"[DOCX 解析失败：{e}]"

    # 其他类型直接尝试读取
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return filename


@router.post("/{candidate_id}/upload-resume")
async def upload_resume_file(
    candidate_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """上传简历文件，提取文本并存入 Resume 表"""
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # 保存上传文件到临时文件
    suffix = os.path.splitext(file.filename or "")[1]
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(tmp_fd, "wb") as tmp:
            content = await file.read()
            tmp.write(content)

        # 提取文本
        parsed_text = _extract_text_from_file(tmp_path, file.filename or "resume.txt")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    # 安全检查：在送入 LLM 解析前对简历原文做输入防护
    is_safe, reason = InputGuard.check(parsed_text)
    if not is_safe:
        raise HTTPException(status_code=400, detail=f"简历内容未通过安全检查：{reason}")

    # 同时尝试调用 workflow 的 parse_resume 做结构化解析
    parsed_data = None
    skills = None
    experience = None
    education = None
    try:
        from src.workflow.recruitment_graph import parse_resume
        state = parse_resume({
            "candidate_id": candidate_id,
            "candidate_name": candidate.name,
            "resume_text": parsed_text,
            "position_requirements": "",
        })
        parsed_data = state.get("parsed_resume")
        if isinstance(parsed_data, dict):
            skills = parsed_data.get("skills")
            experience_list = parsed_data.get("experience")
            if isinstance(experience_list, list):
                experience = "; ".join(
                    str(item) if not isinstance(item, dict)
                    else " - ".join(str(v) for v in item.values())
                    for item in experience_list
                )
            elif experience_list is None:
                experience = None
            else:
                experience = str(experience_list)
            education_list = parsed_data.get("education")
            if isinstance(education_list, list):
                education = "; ".join(
                    str(item) if not isinstance(item, dict)
                    else " - ".join(str(v) for v in item.values())
                    for item in education_list
                )
            elif education_list is None:
                education = None
            else:
                education = str(education_list)
    except Exception as e:
        # LLM 未配置或其他异常时退化为只保存原文
        print(f"[upload_resume] 调用 parse_resume 失败，仅保存原文: {e}")
        parsed_data = {"raw_text": parsed_text[:2000]}

    # 更新或创建 Resume 记录
    resume = db.query(Resume).filter(Resume.candidate_id == candidate_id).first()
    if resume:
        resume.file_name = file.filename
        resume.file_path = f"/resumes/{candidate_id}"
        resume.parsed_data = parsed_data
        resume.skills = skills if skills is not None else resume.skills
        resume.experience = experience or resume.experience
        resume.education = education or resume.education
    else:
        resume = Resume(
            candidate_id=candidate_id,
            file_name=file.filename,
            file_path=f"/resumes/{candidate_id}",
            parsed_data=parsed_data,
            skills=skills if isinstance(skills, list) else None,
            experience=experience,
            education=education,
        )
        db.add(resume)

    # 同步更新 Candidate.resume_text
    candidate.resume_text = parsed_text
    db.commit()
    db.refresh(resume)
    db.refresh(candidate)

    # 输出安全防护：过滤返回结果中的 PII 信息
    return _sanitize_output({
        "message": "简历上传成功",
        "candidate_id": candidate_id,
        "file_name": file.filename,
        "parsed_text": parsed_text,
        "parsed_data": parsed_data,
        "skills": skills,
        "experience": experience,
        "education": education,
    })


def _build_fallback_result(candidate_id: int, candidate_name: str, position: Optional[str], reason: str) -> dict:
    """当 LangGraph 工作流调用失败时的兜底结果（不使用 random）"""
    skill_match_score = 70
    experience_match_score = 70
    education_match_score = 70
    culture_match_score = 70
    overall_score = 70
    final_decision = "待定，进入人才池"

    return {
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "position": position,
        "final_decision": final_decision,
        "overall_score": overall_score,
        "skill_match_score": skill_match_score,
        "experience_match_score": experience_match_score,
        "education_match_score": education_match_score,
        "culture_match_score": culture_match_score,
        "workflow_progress": 100,
        "current_step": "completed",
        "analysis": {
            "skills_analysis": "工作流不可用，使用默认评分",
            "experience_analysis": "工作流不可用，使用默认评分",
            "education_analysis": "工作流不可用，使用默认评分",
            "culture_analysis": "工作流不可用，使用默认评分",
            "recommendation": final_decision,
            "fallback_reason": reason,
        },
        "completed_at": datetime.utcnow().isoformat(),
    }


@router.post("/{candidate_id}/run-workflow")
async def run_recruitment_workflow(
    candidate_id: int,
    body: dict = Body(..., description="职位要求"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    position_requirements = body.get("position_requirements", "")

    # 安全检查：对工作流输入（简历文本 + 职位要求）做输入防护
    for label, text in (("简历文本", candidate.resume_text or ""), ("职位要求", position_requirements)):
        is_safe, reason = InputGuard.check(text)
        if not is_safe:
            raise HTTPException(status_code=400, detail=f"{label}未通过安全检查：{reason}")

    # 评估追踪：记录工作流开始时间
    workflow_start_time = time.time()

    # 创建 WorkflowRun 记录
    workflow_run = WorkflowRun(
        candidate_id=candidate_id,
        status="running",
        current_step="start",
        progress=0,
    )
    db.add(workflow_run)
    db.commit()
    db.refresh(workflow_run)

    # 通知前端工作流开始
    try:
        await notify_workflow_progress(candidate_id, 0, "start", {"candidate_name": candidate.name})
    except Exception:
        pass

    # 调用 LangGraph 工作流
    final_state = None
    fallback_reason = None
    try:
        from src.workflow.recruitment_graph import recruitment_graph
        final_state = recruitment_graph.invoke({
            "candidate_id": candidate_id,
            "candidate_name": candidate.name,
            "resume_text": candidate.resume_text or "",
            "position_requirements": position_requirements,
        })
    except Exception as e:
        fallback_reason = str(e)
        print(f"[run_workflow] LangGraph 调用失败，使用兜底结果: {e}")

    if final_state is None:
        result = _build_fallback_result(
            candidate_id, candidate.name, candidate.position, fallback_reason or "unknown"
        )
    else:
        skill_match_score = int(final_state.get("skill_match_score") or 0)
        culture_match_score = int(final_state.get("culture_match_score") or 0)
        # 经验与教育分数从 skill_match_details 中提取
        skill_details = final_state.get("skill_match_details") or {}
        experience_match_score = int(
            (skill_details.get("experience_analysis") or {}).get("score", skill_match_score)
        )
        education_match_score = int(
            (skill_details.get("education_analysis") or {}).get("score", skill_match_score)
        )

        final_decision = final_state.get("final_decision") or "待定"
        final_recommendation = final_state.get("final_recommendation") or {}
        overall_score = int(final_recommendation.get("overall_score", 0)) if isinstance(final_recommendation, dict) else 0
        if not overall_score:
            overall_score = int(
                (skill_match_score + experience_match_score + education_match_score + culture_match_score) / 4
            )

        # 如果决策为空，则按综合评分给出兜底决策
        if not final_state.get("final_decision"):
            if overall_score >= 85:
                final_decision = "强烈推荐录用"
            elif overall_score >= 75:
                final_decision = "推荐进入面试"
            elif overall_score >= 60:
                final_decision = "待定，进入人才池"
            else:
                final_decision = "不推荐"

        result = {
            "candidate_id": candidate_id,
            "candidate_name": candidate.name,
            "position": candidate.position,
            "final_decision": final_decision,
            "overall_score": overall_score,
            "skill_match_score": skill_match_score,
            "experience_match_score": experience_match_score,
            "education_match_score": education_match_score,
            "culture_match_score": culture_match_score,
            "workflow_progress": int(final_state.get("workflow_progress", 100)),
            "current_step": final_state.get("current_step", "completed"),
            "analysis": {
                "skills_analysis": f"候选人技能与职位要求匹配度为{skill_match_score}%",
                "experience_analysis": f"工作经验匹配度为{experience_match_score}%",
                "education_analysis": f"教育背景匹配度为{education_match_score}%",
                "culture_analysis": f"文化契合度评估为{culture_match_score}%",
                "recommendation": final_decision,
                "final_recommendation": final_recommendation,
            },
            "completed_at": datetime.utcnow().isoformat(),
        }

    # 更新 WorkflowRun
    workflow_run.status = "completed"
    workflow_run.current_step = "completed"
    workflow_run.progress = 100
    workflow_run.results = result
    db.commit()

    # SSE 通知工作流完成 + 招聘决策
    try:
        await notify_workflow_progress(candidate_id, 100, "completed", {"final_decision": result["final_decision"]})
        await notify_hiring_decision(candidate_id, result["final_decision"], result["overall_score"])
    except Exception:
        pass

    # 飞书通知工作流完成
    try:
        await notify_workflow_completed_async(
            candidate.name,
            candidate.position or "",
            result["final_decision"],
            result["overall_score"],
        )
    except Exception as e:
        print(f"[run_workflow] 飞书通知失败: {e}")

    # 评估追踪：记录本次工作流运行的质量指标
    try:
        duration_ms = (time.time() - workflow_start_time) * 1000
        evaluation_tracker.record(
            candidate_id=candidate_id,
            scores={
                "overall_score": result.get("overall_score", 0),
                "skill_match_score": result.get("skill_match_score", 0),
                "experience_match_score": result.get("experience_match_score", 0),
                "education_match_score": result.get("education_match_score", 0),
                "culture_match_score": result.get("culture_match_score", 0),
            },
            decision=result.get("final_decision", ""),
            duration_ms=duration_ms,
        )
    except Exception as e:
        print(f"[run_workflow] 评估追踪记录失败: {e}")

    # 输出安全防护：过滤返回结果中的 PII 信息
    return _sanitize_output(result)
