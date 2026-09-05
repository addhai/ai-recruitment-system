# -*- coding: utf-8 -*-
"""工作流编排层单测：thread_id 生成、结果组装、状态落定、错误路径与自动触发。

get_graph monkeypatch 为假图对象，避免测试中初始化真实 checkpointer。
"""
import asyncio

import pytest

from src.models.database import SessionLocal, Candidate, WorkflowRun
from src.workflow import runner


class _FakeGraph:
    """假图：仅满足 _finalize_outcome 之外的调用，aget_state 不应被触达"""

    async def aget_state(self, config):
        raise AssertionError("fake graph state should not be queried in these tests")


@pytest.fixture(autouse=True)
def _fake_graph(monkeypatch):
    async def _get():
        return _FakeGraph()
    monkeypatch.setattr(runner, "get_graph", _get)


def _mk_candidate(name="runner测试") -> int:
    import uuid
    with SessionLocal() as db:
        # 邮箱加随机后缀：测试库全 session 共享，固定邮箱会撞 UNIQUE 约束
        c = Candidate(name=name, email=f"{name}-{uuid.uuid4().hex[:8]}@t.com", position="Python工程师")
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id


# ---------------------------------------------------------------- 工具函数
def test_new_thread_id_unique_and_prefixed():
    a, b = runner._new_thread_id(), runner._new_thread_id()
    assert a.startswith("wf-") and a != b


def test_thread_id_of_reads_results():
    run = WorkflowRun(results={"thread_id": "wf-abc"})
    assert runner._thread_id_of(run) == "wf-abc"
    assert runner._thread_id_of(WorkflowRun(results=None)) is None
    assert runner._thread_id_of(WorkflowRun(results="不是字典")) is None


def test_build_result_structure():
    with SessionLocal() as db:
        c = db.query(Candidate).first()
        if not c:
            cid = _mk_candidate()
            c = db.query(Candidate).filter(Candidate.id == cid).first()
    result = runner._build_result(
        {"skill_match_score": 80, "culture_match_score": 70, "overall_score": 78,
         "final_decision": "推荐录用", "final_recommendation": {"reasons": ["r"]}},
        c,
    )
    assert result["candidate_id"] == c.id
    assert result["workflow_progress"] == 100
    assert result["skill_match_score"] == 80
    assert result["experience_match_score"] == 80  # 缺省回落到技能分
    assert result["final_decision"] == "推荐录用"
    assert "completed_at" in result


def test_sanitize_masks_phone():
    out = runner._sanitize({"comment": "电话 13800138000 请回访"})
    assert "13800138000" not in str(out)


# ---------------------------------------------------------------- start/resume 错误路径
def test_start_workflow_missing_candidate():
    out = asyncio.run(runner.start_workflow(999999))
    assert out["status"] == "error"


def test_start_workflow_already_active():
    cid = _mk_candidate()
    with SessionLocal() as db:
        db.add(WorkflowRun(candidate_id=cid, status="waiting_human", current_step="await_questionnaire", progress=52))
        db.commit()
    out = asyncio.run(runner.start_workflow(cid))
    assert out["status"] == "already_active"


def test_resume_workflow_missing_candidate():
    out = asyncio.run(runner.resume_workflow(999999, "await_questionnaire", {}))
    assert out["status"] == "error"


def test_resume_workflow_no_active_run():
    cid = _mk_candidate()
    out = asyncio.run(runner.resume_workflow(cid, "await_questionnaire", {}))
    assert out["status"] == "error"
    assert "没有等待人工处理" in out["error"]


def test_resume_workflow_running_not_waiting():
    cid = _mk_candidate()
    with SessionLocal() as db:
        db.add(WorkflowRun(candidate_id=cid, status="running", current_step="parse", progress=10))
        db.commit()
    out = asyncio.run(runner.resume_workflow(cid, "await_questionnaire", {}))
    assert out["status"] == "error"


def test_resume_workflow_thread_id_lost():
    cid = _mk_candidate()
    with SessionLocal() as db:
        db.add(WorkflowRun(candidate_id=cid, status="waiting_human", current_step="await_interview", progress=63, results={}))
        db.commit()
    out = asyncio.run(runner.resume_workflow(cid, "await_interview", {"round": 1}))
    assert out["status"] == "error"
    assert "线程标识丢失" in out["error"]


# ---------------------------------------------------------------- _finalize_outcome
def _mk_run(cid, status="running") -> int:
    with SessionLocal() as db:
        run = WorkflowRun(candidate_id=cid, status=status, current_step="x", progress=50)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id


def test_finalize_waiting_updates_run_and_returns_message():
    cid = _mk_candidate()
    rid = _mk_run(cid)
    out = asyncio.run(runner._finalize_outcome(
        {"status": "waiting", "interrupt": {"type": "await_questionnaire", "questionnaire_id": 1}, "progress": 52},
        rid, cid, "候选人", "wf-t1", 0.0,
    ))
    assert out["status"] == "waiting_human"
    assert "问卷" in out["message"]
    with SessionLocal() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == rid).first()
        assert run.status == "waiting_human"
        assert run.results["thread_id"] == "wf-t1"


def test_finalize_error_marks_failed():
    cid = _mk_candidate()
    rid = _mk_run(cid)
    out = asyncio.run(runner._finalize_outcome(
        {"status": "error", "error": "LLM 超时"}, rid, cid, "候选人", "wf-t2", 0.0,
    ))
    assert out["status"] == "error" and out["error"] == "LLM 超时"
    with SessionLocal() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == rid).first()
        assert run.status == "failed"


def test_finalize_completed_writes_full_result():
    cid = _mk_candidate()
    rid = _mk_run(cid)
    out = asyncio.run(runner._finalize_outcome(
        {"status": "completed", "values": {
            "skill_match_score": 90, "culture_match_score": 80, "overall_score": 85,
            "final_decision": "推荐录用",
        }},
        rid, cid, "候选人", "wf-t3", 0.0,
    ))
    assert out["status"] == "completed"
    assert out["overall_score"] == 85
    with SessionLocal() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == rid).first()
        assert run.status == "completed" and run.progress == 100
        assert run.results["final_decision"] == "推荐录用"


# ---------------------------------------------------------------- 自动触发
def test_trigger_after_upload_respects_flag(monkeypatch):
    called = {"n": 0}

    async def _fake_start(cid, position_requirements=""):
        called["n"] += 1

    monkeypatch.setattr(runner, "start_workflow", _fake_start)
    monkeypatch.setattr(runner.settings, "AUTO_START_WORKFLOW", False)
    asyncio.run(runner.trigger_after_upload(1))
    assert called["n"] == 0

    monkeypatch.setattr(runner.settings, "AUTO_START_WORKFLOW", True)
    asyncio.run(runner.trigger_after_upload(1))
    assert called["n"] == 1


def test_trigger_after_upload_swallows_exception(monkeypatch):
    async def _boom(cid, position_requirements=""):
        raise RuntimeError("启动失败也不应抛出")

    monkeypatch.setattr(runner, "start_workflow", _boom)
    monkeypatch.setattr(runner.settings, "AUTO_START_WORKFLOW", True)
    asyncio.run(runner.trigger_after_upload(1))  # 不抛错即通过
