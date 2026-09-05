# -*- coding: utf-8 -*-
"""工作流 DB 动作层单测：候选人/简历/评估/问卷/面试/人才库的幂等读写。"""
import os
import uuid
from datetime import datetime

from src.models.database import SessionLocal, Candidate, Resume, Interview, User
from src.workflow import db_actions as da


def _mk_candidate(name="测试候选人", resume_text=None, position="Python工程师") -> int:
    with SessionLocal() as db:
        # 邮箱加随机后缀：测试库全 session 共享，固定邮箱会撞 UNIQUE 约束
        c = Candidate(name=name, email=f"{name}-{uuid.uuid4().hex[:8]}@t.com", position=position,
                      resume_text=resume_text or f"{name}的简历文本")
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id


# ---------------------------------------------------------------- 候选人
def test_get_candidate_missing_returns_none():
    assert da.get_candidate(999999) is None


def test_get_candidate_existing():
    cid = _mk_candidate()
    data = da.get_candidate(cid)
    assert data["id"] == cid and data["name"] == "测试候选人"


def test_set_candidate_status():
    cid = _mk_candidate()
    da.set_candidate_status(cid, "interviewing")
    with SessionLocal() as db:
        assert db.query(Candidate).filter(Candidate.id == cid).first().status == "interviewing"


# ---------------------------------------------------------------- 简历
def test_save_resume_parsed_then_get():
    cid = _mk_candidate()
    parsed = {"skills": ["Python"], "name": "测试候选人"}
    da.save_resume_parsed(cid, parsed)
    got = da.get_resume_parsed(cid)
    assert got["skills"] == ["Python"]


def test_get_resume_parsed_missing():
    assert da.get_resume_parsed(999999) is None


# ---------------------------------------------------------------- 评估
def test_upsert_evaluation_insert_then_update():
    cid = _mk_candidate()
    da.upsert_evaluation(cid, "skill_match", 80, "技能扎实")
    da.upsert_evaluation(cid, "skill_match", 90, "技能优秀")  # 幂等更新
    with SessionLocal() as db:
        from src.models.database import Evaluation
        rows = db.query(Evaluation).filter(Evaluation.candidate_id == cid).all()
        assert len(rows) == 1 and rows[0].score == 90


# ---------------------------------------------------------------- 问卷
def test_create_questionnaire_and_save_response():
    cid = _mk_candidate()
    q = da.create_questionnaire(cid, f"技术摸底-{uuid.uuid4().hex[:6]}", "technical", [
        {"question": "Python GIL 是什么？", "answer": ""}
    ])
    assert q["created"] is True and q["questionnaire_id"] is not None

    rid = da.save_questionnaire_response(cid, q["questionnaire_id"], {"q1": "全局解释器锁"}, score=85)
    # 幂等：再次提交更新同一条
    rid2 = da.save_questionnaire_response(cid, q["questionnaire_id"], {"q1": "更新的答案"}, score=None)
    assert rid == rid2


# ---------------------------------------------------------------- 面试
def test_next_workday_10am_skips_weekend():
    # 构造周五 → 顺延应跳过周末
    day = da._next_workday_10am(days_ahead=1)
    assert day.hour == 10 and day.weekday() < 5


def test_schedule_interview_idempotent():
    cid = _mk_candidate()
    first = da.schedule_interview(cid, 1, "Python工程师")
    assert first["created"] is True
    second = da.schedule_interview(cid, 1, "Python工程师")
    assert second["created"] is False
    assert second["interview_id"] == first["interview_id"]


def test_schedule_interview_assigns_interviewer():
    cid = _mk_candidate()
    result = da.schedule_interview(cid, 2, None)
    # conftest 种子库含 admin(可作面试官角色)，应分配到面试官
    assert result["interviewer_id"] is not None
    assert result["scheduled_at"] is not None


def test_pick_interviewer_deterministic():
    with SessionLocal() as db:
        id1 = da._pick_interviewer(db, seed=0)
        id1_again = da._pick_interviewer(db, seed=0)
        assert id1 == id1_again


def test_update_interview_questions_idempotent():
    cid = _mk_candidate()
    result = da.schedule_interview(cid, 1, "岗位")
    iid = result["interview_id"]
    qs1 = [{"question": "题目一", "focus": "考察点"}]
    da.update_interview_questions(iid, qs1)
    qs2 = [{"question": "不应覆盖", "focus": "不应"}]
    da.update_interview_questions(iid, qs2)  # 已有题目不覆盖
    with SessionLocal() as db:
        iv = db.query(Interview).filter(Interview.id == iid).first()
        assert iv.questions == qs1


def test_update_interview_questions_missing_noop():
    da.update_interview_questions(999999, [{"question": "x", "focus": "y"}])  # 不应抛错


def test_complete_interview_record():
    cid = _mk_candidate()
    iid = da.schedule_interview(cid, 1, "岗位")["interview_id"]
    da.complete_interview_record(iid, score=150, feedback="超出满分应被截断", notes="备注")
    with SessionLocal() as db:
        iv = db.query(Interview).filter(Interview.id == iid).first()
        assert iv.status == "completed"
        assert iv.score == 100  # 分数 clamp 到 [0,100]
        assert iv.notes == "备注"


def test_complete_interview_missing_noop():
    da.complete_interview_record(999999, 80, "不存在的面试")  # 不应抛错


def test_get_interview():
    cid = _mk_candidate()
    iid = da.schedule_interview(cid, 1, "岗位")["interview_id"]
    got = da.get_interview(cid, 1)
    assert got is not None and got["interview_id"] == iid
    assert da.get_interview(cid, 9) is None


# ---------------------------------------------------------------- 人才库
def test_upsert_talent_pool():
    cid = _mk_candidate()
    da.upsert_talent_pool(cid, ["备选", "技术好"], "文化分不足")
    # 幂等：再次入库合并标签（去重）并更新备注
    da.upsert_talent_pool(cid, ["更新标签"], "更新备注")
    with SessionLocal() as db:
        from src.models.database import TalentPool
        rows = db.query(TalentPool).filter(TalentPool.candidate_id == cid).all()
        assert len(rows) == 1
        assert set(rows[0].tags) == {"备选", "技术好", "更新标签"}
        assert rows[0].notes == "更新备注"
