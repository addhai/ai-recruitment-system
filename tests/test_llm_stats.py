# -*- coding: utf-8 -*-
"""LLM 成本统计接口测试：按 call_site 聚合、降级率计算、权限边界。"""
import uuid

import pytest

from src.models.database import SessionLocal, LLMCallLog


@pytest.fixture(autouse=True)
def _clean():
    yield
    with SessionLocal() as db:
        db.query(LLMCallLog).delete()
        db.commit()


def _seed(callsite, n=1, cost=0.01, status="ok", degraded=False, tokens=(1000, 200),
          suffix=True):
    """suffix=False 时 call_site 为精确值，便于按站点过滤测试"""
    uid = uuid.uuid4().hex[:6]
    site = f"{callsite}_{uid}" if suffix else callsite
    with SessionLocal() as db:
        for _ in range(n):
            db.add(LLMCallLog(
                call_site=site,
                model="fake",
                input_tokens=tokens[0], output_tokens=tokens[1],
                cost_usd=cost, latency_ms=120,
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
        assert body["totals"]["cost_usd"] == pytest.approx(0.07)
        assert len(body["by_site"]) == 2
        # 按成本倒序
        assert body["by_site"][0]["cost_usd"] >= body["by_site"][1]["cost_usd"]

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
        assert "enabled" in body["budget"] and "limit_usd" in body["budget"]
        # 单价未配置时前端要能提示"预算未生效"
        assert "pricing_configured" in body["budget"]

    def test_empty_returns_zeros(self, client, auth_headers):
        body = client.get("/llm-stats/summary", headers=auth_headers).json()
        assert body["totals"]["calls"] == 0
        assert body["totals"]["cost_usd"] == 0
        assert body["by_site"] == []

    def test_days_parameter_validated(self, client, auth_headers):
        assert client.get("/llm-stats/summary", params={"days": 0},
                          headers=auth_headers).status_code == 422


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
            "id", "created_at", "call_site", "candidate_id", "model",
            "input_tokens", "output_tokens", "cost_usd", "latency_ms",
            "status", "degraded", "usage_missing", "prompt_hash", "error",
        }
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