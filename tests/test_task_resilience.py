# -*- coding: utf-8 -*-
"""任务韧性守门测试：任务中断不再丢弃已完成的工作。

## 问题

普通异常分支只写 {"error": ...} 就把 run 标成 failed，已评估维度全部沉没；
而预算耗尽分支（budget_halted）却会记 assessed_dimensions、转人工、标注可重跑。
同一件事，两种处理——但只有一种是对的：钱已经花了、维度已经评完，
中断不该把这些抹掉。

## 做法

异常中断统一走 partial 终态：

1. 保住已完成的维度（assessed_dimensions）；
2. 候选人转 pending_manual，绝不落到 hired/rejected；
3. 记录失败环节、花费与耗时（failed_step / cost_spent / elapsed_ms）；
4. run 标记 can_rerun，且 partial 不算活跃运行，HR 可以直接重跑。

## 同组守门测试

- 类型安全（字段类型违规不得杀死任务）：test_output_type_safety.py
- 配置口径（空串回退、超时与重试）：test_config_fallback.py
"""
import asyncio
import uuid

from src.models.database import (Candidate, Evaluation, SessionLocal,
                                 TalentPool, WorkflowRun)
from src.workflow import runner
def _mk_candidate() -> int:
    with SessionLocal() as db:
        c = Candidate(name="韧性测试候选人", email=f"res_{uuid.uuid4().hex[:8]}@t.com")
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


# ================================================================ partial 终态
class TestPartialTerminalState:
    def test_error_becomes_partial_not_failed(self):
        """异常中断的终态是 partial，且保住已评估维度"""
        cid, rid = _mk_candidate(), None
        rid = _mk_run(cid)

        out = asyncio.run(runner._finalize_outcome(
            {"status": "error", "error": "注入：写库失败",
             "values": {"skill_match_score": 82, "current_step": "evaluate_education"}},
            rid, cid, "韧性测试候选人", "wf-x", 0.0))

        assert out["status"] == "partial"
        assert out["partial"] is True
        assert "skill_match_score" in out["assessed_dimensions"]
        assert out["failed_step"] == "evaluate_education"
        assert out.get("message")

        with SessionLocal() as db:
            run = db.query(WorkflowRun).filter(WorkflowRun.id == rid).first()
            assert run.status == "partial", "终态必须是 partial，不能是 failed"
            assert run.results["partial"] is True
            assert run.results["can_rerun"] is True
            assert run.results["assessed_dimensions"] == ["skill_match_score"]
            # 归因字段必须落库，否则成功率无法持续监控
            for field in ("failed_step", "failed_call_site", "cost_spent",
                          "elapsed_ms"):
                assert field in run.results, f"缺少归因字段 {field}"

    def test_candidate_goes_pending_manual(self):
        """中断的候选人转人工，绝不能落到 hired/rejected"""
        cid = _mk_candidate()
        rid = _mk_run(cid)
        asyncio.run(runner._finalize_outcome(
            {"status": "error", "error": "boom",
             "values": {"skill_match_score": 70}},
            rid, cid, "韧性测试候选人", "wf-y", 0.0))
        with SessionLocal() as db:
            c = db.query(Candidate).filter(Candidate.id == cid).first()
            assert c.status == "pending_manual"

    def test_talent_pool_records_what_was_done(self):
        """人才池要写清已完成维度与失败环节，HR 才知道还差什么"""
        cid = _mk_candidate()
        rid = _mk_run(cid)
        asyncio.run(runner._finalize_outcome(
            {"status": "error", "error": "注入：写库失败",
             "values": {"skill_match_score": 82, "current_step": "evaluate_education"}},
            rid, cid, "韧性测试候选人", "wf-z", 0.0))
        with SessionLocal() as db:
            pool = db.query(TalentPool).filter(TalentPool.candidate_id == cid).first()
            assert pool is not None
            assert "异常中断" in pool.tags
            assert "evaluate_education" in pool.notes
            assert "skill_match_score" in pool.notes

    def test_partial_is_not_active_run(self):
        """partial 不算活跃运行，HR 可以直接重跑"""
        cid = _mk_candidate()
        rid = _mk_run(cid)
        asyncio.run(runner._finalize_outcome(
            {"status": "error", "error": "boom", "values": {}},
            rid, cid, "韧性测试候选人", "wf-r", 0.0))
        with SessionLocal() as db:
            assert runner._get_active_run(db, cid) is None

    def test_existing_evaluations_preserved(self):
        """中断前已写库的评分必须保留——中断不是数据错误"""
        cid = _mk_candidate()
        rid = _mk_run(cid)
        with SessionLocal() as db:
            db.add(Evaluation(candidate_id=cid, dimension="技能匹配", score=82,
                             comment="中断前已产出"))
            db.commit()
        asyncio.run(runner._finalize_outcome(
            {"status": "error", "error": "boom", "values": {"skill_match_score": 82}},
            rid, cid, "韧性测试候选人", "wf-s", 0.0))
        with SessionLocal() as db:
            ev = db.query(Evaluation).filter(Evaluation.candidate_id == cid,
                                            Evaluation.dimension == "技能匹配").first()
            assert ev is not None and ev.score == 82

    def test_budget_halted_still_distinct(self):
        """预算耗尽仍是 budget_halted，不能被 partial 吞掉"""
        cid = _mk_candidate()
        rid = _mk_run(cid)
        out = asyncio.run(runner._finalize_outcome(
            {"status": "budget_exhausted", "error": "预算用尽",
             "values": {"skill_match_score": 70}},
            rid, cid, "韧性测试候选人", "wf-b", 0.0))
        assert out["status"] == "budget_halted"
        with SessionLocal() as db:
            run = db.query(WorkflowRun).filter(WorkflowRun.id == rid).first()
            assert run.status == "budget_halted"

