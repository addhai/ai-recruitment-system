# -*- coding: utf-8 -*-
"""LLM 成本统计接口测试：按 call_site 聚合、降级率计算、权限边界。"""
import uuid

import pytest

from src.models.database import SessionLocal, LLMCallLog
from src.services import llm_config

# 当前生效计价币种：成本聚合只统计与它一致的行
_CURRENT = llm_config.get_effective_config()["currency"]
# 另一个币种：用于验证"不被混算"，不硬编码具体值以免测试环境币种不同就误判
_OTHER = "USD" if _CURRENT != "USD" else "CNY"


@pytest.fixture(autouse=True)
def _clean():
    yield
    with SessionLocal() as db:
        db.query(LLMCallLog).delete()
        db.commit()


def _seed(callsite, n=1, cost=0.01, status="ok", degraded=False, tokens=(1000, 200),
          suffix=True, currency=_CURRENT):
    """suffix=False 时 call_site 为精确值，便于按站点过滤测试。

    currency 默认取当前生效配置的币种：成本聚合只认与当前币种一致的行，
    种子行不写币种会被当成"未知币种"而排除在成本之外。
    """
    uid = uuid.uuid4().hex[:6]
    site = f"{callsite}_{uid}" if suffix else callsite
    with SessionLocal() as db:
        for _ in range(n):
            db.add(LLMCallLog(
                call_site=site,
                model="fake",
                input_tokens=tokens[0], output_tokens=tokens[1],
                cost=cost, currency=currency, latency_ms=120,
                status=status, degraded=degraded, usage_missing=False,
            ))
        db.commit()
    return site


class TestSummary:
    def test_aggregates_by_call_site(self, client, auth_headers):
        _seed("parse_resume", n=3, cost=0.01)
        _seed("evaluate_skill_match", n=2, cost=0.02)

        body = client.get("/llm-stats/summary", params={"days": 7},
                          headers=auth_headers).json()
        assert body["totals"]["calls"] == 5
        assert body["totals"]["cost"] == pytest.approx(0.07)
        assert len(body["by_site"]) == 2
        # 按成本倒序
        assert body["by_site"][0]["cost"] >= body["by_site"][1]["cost"]

    def test_degraded_rate_computed(self, client, auth_headers):
        site = _seed("assess_cultural_fit", n=4, degraded=False)
        with SessionLocal() as db:
            db.query(LLMCallLog).filter(LLMCallLog.call_site == site).first()
            row = db.query(LLMCallLog).filter(LLMCallLog.call_site == site).first()
            row.degraded = True
            row.status = "failed"
            db.commit()

        body = client.get("/llm-stats/summary", headers=auth_headers).json()
        entry = next(e for e in body["by_site"] if e["call_site"] == site)
        assert entry["degraded"] == 1
        assert entry["degraded_rate"] == pytest.approx(0.25), "降级率必须能被单独读出"

    def test_failed_rate_computed(self, client, auth_headers):
        site = _seed("generate_hiring_decision", n=2)
        with SessionLocal() as db:
            row = db.query(LLMCallLog).filter(LLMCallLog.call_site == site).first()
            row.status = "failed"
            db.commit()
        body = client.get("/llm-stats/summary", headers=auth_headers).json()
        assert body["totals"]["failed"] == 1
        assert body["totals"]["failed_rate"] == pytest.approx(0.5)

    def test_budget_state_exposed(self, client, auth_headers):
        body = client.get("/llm-stats/summary", headers=auth_headers).json()
        assert "enabled" in body["budget"] and "limit" in body["budget"]
        # 单价未配置时前端要能提示"预算未生效"
        assert "pricing_configured" in body["budget"]

    def test_empty_returns_zeros(self, client, auth_headers):
        body = client.get("/llm-stats/summary", headers=auth_headers).json()
        assert body["totals"]["calls"] == 0
        assert body["totals"]["cost"] == 0
        assert body["by_site"] == []

    def test_days_parameter_validated(self, client, auth_headers):
        assert client.get("/llm-stats/summary", params={"days": 0},
                          headers=auth_headers).status_code == 422


class TestCurrencyScopedCost:
    """成本聚合必须按币种隔离。

    回归背景：原实现把所有行的 cost 直接相加，再用当前配置币种去标注。
    一旦币种变更过，¥ 与 $ 会被加成一个数并贴上错误的单位——
    金额直接错，且看不出错。
    """

    def test_foreign_currency_excluded_from_cost(self, client, auth_headers):
        _seed("cn_site", n=2, cost=0.01, currency=_CURRENT)
        _seed("us_site", n=3, cost=1.00, currency=_OTHER)

        body = client.get("/llm-stats/summary", params={"days": 7},
                          headers=auth_headers).json()

        # 次数与 token 与币种无关，仍统计全部行
        assert body["totals"]["calls"] == 5
        # 成本只累计当前币种，另一币种的 3.00 不能被加进来
        assert body["totals"]["cost"] == pytest.approx(0.02)
        # 但也不能静默丢掉：单独报出来
        assert body["totals"]["foreign_cost"] == pytest.approx(3.00)
        assert body["totals"]["foreign_currencies"] == [_OTHER]

    def test_null_currency_rows_excluded_but_counted(self, client, auth_headers):
        """多币种改造前的历史行 currency 为 NULL：金额不参与求和，次数仍计

        回归 SQL 三值逻辑：`currency = 'X'` 对 NULL 行返回 NULL 而非 FALSE，
        若用 `NOT (currency = 'X')` 判"不在当前币种"，NULL 行会两头都不算。
        """
        _seed("legacy_site", n=2, cost=0.5, currency=None)
        _seed("now_site", n=1, cost=0.01, currency=_CURRENT)

        body = client.get("/llm-stats/summary", params={"days": 7},
                          headers=auth_headers).json()
        assert body["totals"]["calls"] == 3
        assert body["totals"]["cost"] == pytest.approx(0.01)
        # NULL 币种归到 unattributed，不能被悄悄漏掉
        assert body["totals"]["unattributed_cost"] == pytest.approx(1.0)
        assert body["totals"]["excluded_cost"] == pytest.approx(1.0)
        # 币种未知不进 foreign_currencies（那是"已知的其它币种"）
        assert body["totals"]["foreign_currencies"] == []

    def test_by_site_cost_is_currency_scoped(self, client, auth_headers):
        site = _seed("mixed_source", n=1, cost=5.0, suffix=False, currency=_OTHER)
        body = client.get("/llm-stats/summary", params={"days": 7},
                          headers=auth_headers).json()
        row = next(r for r in body["by_site"] if r["call_site"] == site)
        assert row["calls"] == 1
        assert row["cost"] == 0.0
        assert row["excluded_cost"] == pytest.approx(5.0)


class TestRecentCallsByThread:
    """按工作流运行过滤：一次简历评估会发 4~8 次调用，要能把它们串起来看。

    只有 candidate_id 时同一候选人重跑就混在一起，所以必须有 thread_id 维度。
    """

    def _seed_traced(self, thread_id, sites):
        with SessionLocal() as db:
            for site in sites:
                db.add(LLMCallLog(call_site=site, model="m", cost=0.001,
                                  currency=_CURRENT, thread_id=thread_id,
                                  latency_ms=10, status="ok",
                                  degraded=False, usage_missing=False))
            db.commit()

    def test_thread_id_returned_in_rows(self, client, auth_headers):
        self._seed_traced("wf-aaa", ["parse_resume"])
        rows = client.get("/llm-stats/calls", headers=auth_headers).json()
        assert any(r["thread_id"] == "wf-aaa" for r in rows)
        assert "thread_id" in rows[0], "明细必须带上运行标识才能下钻"

    def test_filter_returns_only_that_run(self, client, auth_headers):
        self._seed_traced("wf-aaa", ["parse_resume", "extract_skills"])
        self._seed_traced("wf-bbb", ["evaluate_education"])

        rows = client.get("/llm-stats/calls", params={"thread_id": "wf-aaa"},
                          headers=auth_headers).json()
        assert len(rows) == 2
        assert {r["thread_id"] for r in rows} == {"wf-aaa"}
        assert {r["call_site"] for r in rows} == {"parse_resume", "extract_skills"}

    def test_filter_orders_chronologically(self, client, auth_headers):
        """按运行查时用时间正序——这样看起来就是实际执行顺序"""
        self._seed_traced("wf-order", ["a_first", "b_second", "c_third"])
        rows = client.get("/llm-stats/calls", params={"thread_id": "wf-order"},
                          headers=auth_headers).json()
        assert [r["call_site"] for r in rows] == ["a_first", "b_second", "c_third"]

    def test_untraced_rows_have_null_thread(self, client, auth_headers):
        """知识库问答 / JD 解析没有工作流上下文，该列应为空而不是假值"""
        _seed("knowledge_qa", n=1, suffix=False)
        rows = client.get("/llm-stats/calls", headers=auth_headers).json()
        row = next(r for r in rows if r["call_site"] == "knowledge_qa")
        assert row["thread_id"] is None


class TestRecentCalls:
    def test_returns_metadata_not_content(self, client, auth_headers):
        site = _seed("parse_resume", n=1, suffix=False)
        with SessionLocal() as db:
            row = db.query(LLMCallLog).filter(LLMCallLog.call_site == site).first()
            row.prompt_hash = "a" * 64
            db.commit()

        rows = client.get("/llm-stats/calls", headers=auth_headers).json()
        assert len(rows) == 1
        r = rows[0]
        # 只应有元数据，绝不能回传 prompt 全文或模型输出
        assert set(r.keys()) <= {
            "id", "created_at", "call_site", "candidate_id", "thread_id",
            "model", "model_served", "input_tokens", "output_tokens", "cost",
            "currency", "latency_ms", "status", "degraded", "usage_missing",
            "prompt_hash", "error",
        }
        assert "model_served" in r, "实际服务的模型版本属于元数据，应回传"
        assert len(r["prompt_hash"]) == 12, "prompt 只存哈希前 12 位"

    def test_filter_by_call_site(self, client, auth_headers):
        _seed("alpha", n=2, suffix=False)
        _seed("beta", n=1, suffix=False)
        rows = client.get("/llm-stats/calls", params={"call_site": "alpha"},
                          headers=auth_headers).json()
        assert len(rows) == 2
        assert all(r["call_site"] == "alpha" for r in rows)


class TestPermissions:
    def test_unauthenticated(self, client):
        assert client.get("/llm-stats/summary").status_code == 401

    def test_viewer_forbidden(self, client):
        """成本数据属敏感运营信息，只对 HR/管理员开放"""
        u = f"viewer_{uuid.uuid4().hex[:8]}"
        client.post("/auth/register", json={"username": u, "email": f"{u}@t.com",
                                            "password": "testpass123", "role": "viewer"})
        tok = client.post("/auth/login", data={"username": u, "password": "testpass123"}
                          ).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert client.get("/llm-stats/summary", headers=h).status_code == 403
        assert client.get("/llm-stats/calls", headers=h).status_code == 403

    def test_interviewer_forbidden(self, client):
        u = f"itv_{uuid.uuid4().hex[:8]}"
        client.post("/auth/register", json={"username": u, "email": f"{u}@t.com",
                                            "password": "testpass123", "role": "interviewer"})
        tok = client.post("/auth/login", data={"username": u, "password": "testpass123"}
                          ).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert client.get("/llm-stats/summary", headers=h).status_code == 403