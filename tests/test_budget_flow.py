# -*- coding: utf-8 -*-
"""预算耗尽的工作流流转测试：_drive 捕获 → 转人工终态 → 不产出招聘决策。

核心不变量：
1. BudgetExceeded 必须在兜底 except Exception 之前被精确捕获
2. 候选人停在 pending_manual，绝不落到 hired/rejected
3. 中断前已写入的 Evaluation 保留（预算耗尽不是数据错误）
4. budget_halted 不算活跃运行，提高预算后可直接重跑
"""
import asyncio
import uuid

import pytest

from src.models.database import SessionLocal, Candidate, Evaluation, TalentPool, WorkflowRun
from src.services import budget
from src.workflow import runner


def _mk_candidate() -> int:
    with SessionLocal() as db:
        c = Candidate(name="预算测试候选人", email=f"bg_{uuid.uuid4().hex[:8]}@t.com")
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id


def _mk_run(cid: int) -> int:
    with SessionLocal() as db:
        r = WorkflowRun(candidate_id=cid, status="running", current_step="start")
        db.add(r)
        db.commit()
        db.refresh(r)
        return r.id


# ================================================================ _drive 捕获
class TestDriveCatchesBudget:
    def test_budget_not_swallowed_by_generic_handler(self, monkeypatch):
        """BudgetExceeded 不能被兜底 except Exception 吞成普通 error"""

        class _G:
            def astream(self, *a, **kw):
                raise budget.BudgetExceeded("预算用尽")

            async def aget_state(self, _cfg):
                raise AssertionError("不应走到 aget_state")

        cid = _mk_candidate()
        rid = _mk_run(cid)
        out = asyncio.run(runner._drive(_G(), {}, rid, cid, "预算测试候选人",
                                        start_input={}))
        assert out["status"] == "budget_exhausted"
        assert out["status"] != "error", "预算耗尽必须与普通失败区分开"

    def test_generic_failure_still_error(self):
        """非预算类异常仍走原 error 分支，行为不变"""

        class _G:
            def astream(self, *a, **kw):
                raise RuntimeError("普通故障")

            async def aget_state(self, _cfg):
                raise AssertionError("不应走到")

        cid = _mk_candidate()
        rid = _mk_run(cid)
        out = asyncio.run(runner._drive(_G(), {}, rid, cid, "x", start_input={}))
        assert out["status"] == "error"


# ================================================================ 终态转人工
class TestFinalizeBudgetHalted:
    def test_candidate_goes_pending_manual(self, monkeypatch):
        cid, rid = _mk_candidate(), None
        rid = _mk_run(cid)

        out = asyncio.run(runner._finalize_outcome(
            {"status": "budget_exhausted",
             "error": "预算用尽",
             "values": {"skill_match_score": 82, "culture_match_score": 70,
                        "current_step": "evaluate_education"}},
            rid, cid, "预算测试候选人", "wf-x", 0.0))

        assert out["status"] == "budget_halted"
        assert out["budget_halted"] is True
        assert "skill_match_score" in out["assessed_dimensions"]
        assert out.get("message")

        with SessionLocal() as db:
            c = db.query(Candidate).filter(Candidate.id == cid).first()
            assert c.status == "pending_manual", "绝不能落到 hired/rejected"
            run = db.query(WorkflowRun).filter(WorkflowRun.id == rid).first()
            assert run.status == "budget_halted"
            assert run.results["budget_halted"] is True
            assert run.results["can_rerun"] is True

    def test_talent_pool_tagged(self):
        cid, rid = _mk_candidate(), None
        rid = _mk_run(cid)
        asyncio.run(runner._finalize_outcome(
            {"status": "budget_exhausted", "error": "x",
             "values": {"skill_match_score": 80}},
            rid, cid, "候选人", "wf-y", 0.0))
        with SessionLocal() as db:
            pool = db.query(TalentPool).filter(TalentPool.candidate_id == cid).first()
            assert pool is not None
            assert "预算暂停" in pool.tags

    def test_existing_evaluations_preserved(self):
        """中断前已算出的评分必须保留——预算耗尽不是数据错误"""
        cid, rid = _mk_candidate(), None
        rid = _mk_run(cid)
        with SessionLocal() as db:
            db.add(Evaluation(candidate_id=cid, dimension="技能匹配", score=82,
                             comment="中断前已产出"))
            db.commit()

        asyncio.run(runner._finalize_outcome(
            {"status": "budget_exhausted", "error": "x",
             "values": {"skill_match_score": 82}},
            rid, cid, "候选人", "wf-z", 0.0))

        with SessionLocal() as db:
            ev = db.query(Evaluation).filter(Evaluation.candidate_id == cid,
                                            Evaluation.dimension == "技能匹配").first()
            assert ev is not None and ev.score == 82, "已产生的评分不应被回滚"

    def test_budget_halted_is_not_active_run(self):
        """budget_halted 不算活跃，提高预算后能直接重跑"""
        cid, rid = _mk_candidate(), None
        rid = _mk_run(cid)
        asyncio.run(runner._finalize_outcome(
            {"status": "budget_exhausted", "error": "x", "values": {}},
            rid, cid, "候选人", "wf-r", 0.0))
        with SessionLocal() as db:
            active = runner._get_active_run(db, cid)
            assert active is None, "budget_halted 不应阻塞重跑"