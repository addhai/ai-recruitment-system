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


def _mk_candidate(name="runner测试", jd_id=None) -> int:
    import uuid
    with SessionLocal() as db:
        # 邮箱加随机后缀：测试库全 session 共享，固定邮箱会撞 UNIQUE 约束
        c = Candidate(name=name, email=f"{name}-{uuid.uuid4().hex[:8]}@t.com",
                      position="Python工程师", job_description_id=jd_id)
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
    assert result["final_decision"] == "推荐录用"
    assert "completed_at" in result
    # 未评估的维度必须是 None，不能回落到技能分——
    # 展示一个从未考过的 80 分，与 compute_overall 排除它的做法自相矛盾
    assert result["experience_match_score"] is None
    assert "未评估" in result["analysis"]["experience_analysis"]


def test_build_result_marks_unconstrained_dimensions():
    """岗位未设限的维度要在结果里标出来，前端据此说明是「不参与评分」而非「未评估」"""
    with SessionLocal() as db:
        c = db.query(Candidate).first()
        if not c:
            cid = _mk_candidate()
            c = db.query(Candidate).filter(Candidate.id == cid).first()
    result = runner._build_result(
        {"skill_match_score": 80, "experience_score": 75,
         "culture_match_score": None, "overall_score": 78,
         "unconstrained_dimensions": ["culture_match_score"],
         "final_decision": "推荐录用", "final_recommendation": {}},
        c,
    )
    assert result["culture_match_score"] is None
    assert result["unconstrained_dimensions"] == ["culture_match_score"]
    assert "岗位未设限" in result["analysis"]["culture_analysis"]


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


def _mk_run_with_results(cid, results) -> int:
    with SessionLocal() as db:
        run = WorkflowRun(candidate_id=cid, status="running", current_step="x",
                          progress=40, results=results)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id


# 下面三条守同一个缺口：**口径信息必须活过挂起与异常结束**。
# 这三条路径此前都是整体覆盖 results，只写自己关心的键，
# 于是一旦流程停在"等问卷/等面试"，启动时写入的 thread_id / jd_profile /
# scoring_version / scoring_fingerprint 全被抹掉。挂起是最常见的中途状态，
# 不是罕见分支；抹掉之后复核列表读 scoring_version 会拿到 None，
# 运行记录也再对不上 checkpoint 的 thread_id。

def test_finalize_waiting_keeps_scoring_metadata():
    cid = _mk_candidate("挂起保留口径")
    rid = _mk_run_with_results(cid, {
        "thread_id": "wf-keep", "jd_profile": {"basic": {"education_required": "本科"}},
        "scoring_version": "v2", "scoring_fingerprint": "abcdef0123456789",
    })
    asyncio.run(runner._finalize_outcome(
        {"status": "waiting", "interrupt": {"type": "await_interview", "round": 1},
         "progress": 60},
        rid, cid, "候选人", "wf-keep", 0.0,
    ))
    with SessionLocal() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == rid).first()
        assert run.status == "waiting_human"
        assert run.results["interrupt"]["type"] == "await_interview", "挂起信息要写进去"
        assert run.results["thread_id"] == "wf-keep"
        assert run.results["scoring_version"] == "v2"
        assert run.results["scoring_fingerprint"] == "abcdef0123456789"
        assert run.results["jd_profile"]["basic"]["education_required"] == "本科"


def test_finalize_error_keeps_scoring_metadata():
    cid = _mk_candidate("失败保留口径")
    rid = _mk_run_with_results(cid, {"thread_id": "wf-err",
                                     "scoring_fingerprint": "fp-error"})
    asyncio.run(runner._finalize_outcome(
        {"status": "error", "error": "LLM 超时"}, rid, cid, "候选人", "wf-err", 0.0))
    with SessionLocal() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == rid).first()
        assert run.status == "failed"
        assert run.results["error"] == "LLM 超时"
        assert run.results["thread_id"] == "wf-err"
        assert run.results["scoring_fingerprint"] == "fp-error"


def test_finalize_budget_halted_keeps_scoring_metadata():
    cid = _mk_candidate("预算中止保留口径")
    rid = _mk_run_with_results(cid, {"thread_id": "wf-budget",
                                     "scoring_fingerprint": "fp-budget"})
    out = asyncio.run(runner._finalize_outcome(
        {"status": "budget_exhausted", "error": "预算不足",
         "values": {"current_step": "evaluate_skill_match"}},
        rid, cid, "候选人", "wf-budget", 0.0))
    assert out["status"] == "budget_halted"
    with SessionLocal() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == rid).first()
        assert run.status == "budget_halted"
        assert run.results["budget_halted"] is True
        assert run.results["scoring_fingerprint"] == "fp-budget"
        assert run.results["thread_id"] == "wf-budget"


def _mk_active_jd() -> int:
    """建一条可用的 active JD（解析成功），供强制绑定的路径使用"""
    from src.models.database import JobDescription
    with SessionLocal() as db:
        jd = JobDescription(
            title="Python工程师", department="技术部", status="active", parse_status="parsed",
            raw_text="精通 Python",
            parsed_data={"required_skills": [{"skill": "Python", "evidence": "精通 Python"}]},
        )
        db.add(jd)
        db.commit()
        db.refresh(jd)
        return jd.id


def test_start_workflow_requires_active_jd(monkeypatch):
    """回归：未绑定 active JD 时 start_workflow 直接返回 error，不创建运行记录

    同时守住 db_actions 的遮蔽陷阱：函数内若残留局部
    `from src.workflow import db_actions`，模块级导入会被遮蔽，
    JD 校验会抛 UnboundLocalError，被 trigger_after_upload 的兜底吞掉，
    表现为"上传成功但工作流静默不跑"。
    """
    cid = _mk_candidate("未绑定JD")
    out = asyncio.run(runner.start_workflow(cid))
    assert out["status"] == "error"
    assert "岗位 JD" in out["error"]
    with SessionLocal() as db:
        assert db.query(WorkflowRun).filter(WorkflowRun.candidate_id == cid).count() == 0


def test_start_workflow_accepts_active_jd(monkeypatch):
    """绑定 active JD 后应通过 JD 门禁（checkpointer 用假图避免真实初始化）"""
    driven = {}

    async def _fake_drive(graph, config, run_id, cid, name, start_input=None, resume_value=None):
        driven["jd_profile"] = start_input.get("jd_profile")
        driven["job_description_id"] = start_input.get("job_description_id")
        return {"status": "error", "error": "驱动已到达（测试不关心图执行）"}

    monkeypatch.setattr(runner, "_drive", _fake_drive)
    jd_id = _mk_active_jd()
    cid = _mk_candidate("已绑定JD", jd_id)
    out = asyncio.run(runner.start_workflow(cid))
    assert driven["job_description_id"] == jd_id, "应把绑定的 JD 注入工作流 state"
    assert driven["jd_profile"], "jd_profile 必须传入节点，否则匹配又会退回岗位名"


# ---------------------------------------------------------------- 自动触发
def test_trigger_after_upload_respects_flag(monkeypatch):
    called = {"n": 0}

    async def _fake_start(cid, position_requirements="", job_description_id=None):
        called["n"] += 1

    monkeypatch.setattr(runner, "start_workflow", _fake_start)
    monkeypatch.setattr(runner.settings, "AUTO_START_WORKFLOW", False)
    jd_id = _mk_active_jd()
    asyncio.run(runner.trigger_after_upload(_mk_candidate("开关关闭", jd_id)))
    assert called["n"] == 0

    monkeypatch.setattr(runner.settings, "AUTO_START_WORKFLOW", True)
    asyncio.run(runner.trigger_after_upload(_mk_candidate("开关开启", jd_id)))
    assert called["n"] == 1


def test_trigger_after_upload_skips_when_no_active_jd(monkeypatch):
    """回归：强制绑定 JD 后，没绑定岗位的候选人上传简历不再自动跑工作流。

    此前无论有没有 JD 都会自动触发，匹配依据只是岗位名字符串。
    现在应改为推送 jd_missing 通知引导前端补录。
    """
    called = {"n": 0}

    async def _fake_start(cid, position_requirements="", job_description_id=None):
        called["n"] += 1

    notified = {}

    async def _fake_notify(cid, name):
        notified["called"] = True

    monkeypatch.setattr(runner, "start_workflow", _fake_start)
    monkeypatch.setattr(runner.settings, "AUTO_START_WORKFLOW", True)
    monkeypatch.setattr("src.sse.notification.notify_jd_missing", _fake_notify)

    asyncio.run(runner.trigger_after_upload(_mk_candidate("无JD候选人", None)))

    assert called["n"] == 0, "未绑定 JD 时不应启动工作流"
    assert notified.get("called") is True, "应推送 jd_missing 通知引导绑定 JD"


def test_trigger_after_upload_swallows_exception(monkeypatch):
    async def _boom(cid, position_requirements=""):
        raise RuntimeError("启动失败也不应抛出")

    monkeypatch.setattr(runner, "start_workflow", _boom)
    monkeypatch.setattr(runner.settings, "AUTO_START_WORKFLOW", True)
    asyncio.run(runner.trigger_after_upload(1))  # 不抛错即通过
