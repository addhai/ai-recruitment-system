# -*- coding: utf-8 -*-
"""岗位 JD API 与人工复核接口测试。

覆盖：JD CRUD、解析失败不可启用、已有候选人的 JD 不可删除、
候选人绑定后匹配门禁放行、待复核队列与裁决闭环（裁决不重跑 LLM）。
"""
import asyncio
import os

import pytest

from src.models.database import SessionLocal, JobDescription, Candidate, TalentPool, Evaluation
from src.services import job_parser as jp
from tests.helpers import login_headers


@pytest.fixture
def no_autostart(monkeypatch):
    """屏蔽上传后的自动工作流触发"""
    monkeypatch.setattr("src.api.candidates.trigger_after_upload", lambda cid: None)


def _create_candidate(client, headers, name="JD测试", **kw):
    payload = {"name": name, "email": f"{name}_{os.urandom(3).hex()}@t.com"}
    payload.update(kw)
    r = client.post("/candidates/", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _mk_position(client, headers, title="后端工程师") -> int:
    r = client.post("/positions/", json={"title": title}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _mk_jd(client, headers, title="后端工程师", raw="精通 Python"):
    """JD 必须挂在某个岗位下（岗位 → JD 两层模型）"""
    position_id = _mk_position(client, headers, title=title)
    r = client.post("/job_descriptions/",
                    data={"position_id": str(position_id), "title": title, "raw_text": raw},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _fake_parse_ok(monkeypatch):
    async def _fake(text, *, call_site=None, **__):
        return {
            "basic": {"education_required": "本科及以上", "department": "技术部",
                      "experience_years_required": "3年以上",
                      "experience_years_preferred": "", "location": "",
                      "employment_type": "", "headcount": 0, "education_preferred": ""},
            "responsibilities": ["负责后端服务开发"],
            "required_skills": [{"skill": "Python", "evidence": "精通 Python"}],
            "preferred_skills": [],
            "tech_stack": ["Python"], "domain_knowledge": [], "soft_skills": [],
            "culture_values": ["严谨负责"], "keywords": [],
        }, None
    # 注意：API 模块用 from ... import 直接绑定了名字，
    # 必须打在 src.api.job_descriptions 上，打在 job_parser 上不生效
    monkeypatch.setattr("src.api.job_descriptions.parse_job_description", _fake)


# ================================================================ JD CRUD
class TestJobDescriptionCrud:
    def test_create_and_list(self, client, auth_headers):
        jd = _mk_jd(client, auth_headers)
        assert jd["status"] == "draft" and jd["parse_status"] == "pending"
        r = client.get("/job_descriptions/", headers=auth_headers)
        assert r.status_code == 200
        assert any(x["id"] == jd["id"] for x in r.json())

    def test_create_requires_text_or_file(self, client, auth_headers):
        position_id = _mk_position(client, auth_headers, title="空岗位")
        r = client.post("/job_descriptions/",
                        data={"position_id": str(position_id), "title": "空岗位"},
                        headers=auth_headers)
        assert r.status_code == 400

    def test_create_requires_valid_position(self, client, auth_headers):
        """JD 必须挂在存在的岗位下"""
        r = client.post("/job_descriptions/",
                        data={"position_id": "999999", "title": "孤儿JD", "raw_text": "精通 Python"},
                        headers=auth_headers)
        assert r.status_code == 404

    def test_parse_success_then_activate(self, client, auth_headers, monkeypatch):
        _fake_parse_ok(monkeypatch)
        jd = _mk_jd(client, auth_headers)
        r = client.post(f"/job_descriptions/{jd['id']}/parse", headers=auth_headers)
        assert r.status_code == 200 and r.json()["parse_status"] == "parsed"
        # 人工核对：画像里能看到原文依据
        ev = r.json()["parsed_data"]["required_skills"][0]["evidence"]
        assert ev == "精通 Python"
        r = client.post(f"/job_descriptions/{jd['id']}/activate", headers=auth_headers)
        assert r.status_code == 200 and r.json()["status"] == "active"

    def test_activate_blocked_before_parse(self, client, auth_headers):
        jd = _mk_jd(client, auth_headers)
        r = client.post(f"/job_descriptions/{jd['id']}/activate", headers=auth_headers)
        assert r.status_code == 400 and "解析" in r.json()["detail"]

    def test_parse_failure_records_error_and_blocks_activate(self, client, auth_headers, monkeypatch):
        async def _fail(text, *, call_site=None, **__):
            return None, "未能提取到技能项"
        monkeypatch.setattr("src.api.job_descriptions.parse_job_description", _fail)
        jd = _mk_jd(client, auth_headers)
        r = client.post(f"/job_descriptions/{jd['id']}/parse", headers=auth_headers)
        assert r.status_code == 400
        r = client.post(f"/job_descriptions/{jd['id']}/activate", headers=auth_headers)
        assert r.status_code == 400

    def test_update_raw_text_resets_parse(self, client, auth_headers, monkeypatch):
        _fake_parse_ok(monkeypatch)
        jd = _mk_jd(client, auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/parse", headers=auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/activate", headers=auth_headers)
        r = client.put(f"/job_descriptions/{jd['id']}",
                       json={"raw_text": "换了新要求：精通 Go"}, headers=auth_headers)
        assert r.status_code == 200
        # 原文改了，旧画像失效，必须重新解析
        assert r.json()["parse_status"] == "pending"
        assert r.json()["status"] == "draft"

    def test_update_rejects_profile_without_evidence(self, client, auth_headers):
        jd = _mk_jd(client, auth_headers)
        r = client.put(f"/job_descriptions/{jd['id']}",
                       json={"parsed_data": {"required_skills": [{"skill": "Rust", "evidence": ""}]}},
                       headers=auth_headers)
        assert r.status_code == 400

    def test_archive_and_delete(self, client, auth_headers, monkeypatch):
        _fake_parse_ok(monkeypatch)
        jd = _mk_jd(client, auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/parse", headers=auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/activate", headers=auth_headers)
        r = client.post(f"/job_descriptions/{jd['id']}/archive", headers=auth_headers)
        assert r.json()["status"] == "archived"
        assert client.delete(f"/job_descriptions/{jd['id']}", headers=auth_headers).status_code == 200

    def test_delete_blocked_when_candidates_bound(self, client, auth_headers, monkeypatch):
        """已有候选人绑定的 JD 不能删，否则候选人的匹配依据会凭空消失"""
        _fake_parse_ok(monkeypatch)
        jd = _mk_jd(client, auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/parse", headers=auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/activate", headers=auth_headers)
        _create_candidate(client, auth_headers, name="绑定候选人", job_description_id=jd["id"])
        r = client.delete(f"/job_descriptions/{jd['id']}", headers=auth_headers)
        assert r.status_code == 400 and "停用" in r.json()["detail"]

    def test_candidate_count_in_list(self, client, auth_headers, monkeypatch):
        _fake_parse_ok(monkeypatch)
        jd = _mk_jd(client, auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/parse", headers=auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/activate", headers=auth_headers)
        _create_candidate(client, auth_headers, name="计数候选人", job_description_id=jd["id"])
        r = client.get(f"/job_descriptions/{jd['id']}", headers=auth_headers)
        assert r.json()["candidate_count"] == 1

    def test_unauthorized(self, client):
        assert client.get("/job_descriptions/").status_code == 401


# ================================================================ 匹配门禁
class TestMatchGating:
    def test_run_workflow_requires_bound_active_jd(self, client, auth_headers):
        cid = _create_candidate(client, auth_headers, name="无岗位候选人")
        r = client.post(f"/candidates/{cid}/run-workflow",
                        json={"position_requirements": "Python"}, headers=auth_headers)
        assert r.status_code == 400 and "岗位 JD" in r.json()["detail"]

    def test_draft_jd_does_not_pass_gate(self, client, auth_headers, monkeypatch):
        """未启用（draft）的 JD 不能用于匹配"""
        _fake_parse_ok(monkeypatch)
        jd = _mk_jd(client, auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/parse", headers=auth_headers)
        # 故意不 activate
        cid = _create_candidate(client, auth_headers, name="草稿岗位候选人",
                                job_description_id=jd["id"])
        r = client.post(f"/candidates/{cid}/run-workflow",
                        json={}, headers=auth_headers)
        assert r.status_code == 400 and "岗位 JD" in r.json()["detail"]

    def test_candidate_can_bind_jd_via_update(self, client, auth_headers, monkeypatch):
        _fake_parse_ok(monkeypatch)
        jd = _mk_jd(client, auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/parse", headers=auth_headers)
        client.post(f"/job_descriptions/{jd['id']}/activate", headers=auth_headers)
        cid = _create_candidate(client, auth_headers, name="后绑定候选人")
        r = client.put(f"/candidates/{cid}", json={"job_description_id": jd["id"]},
                       headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["job_description_id"] == jd["id"]

    def test_upload_without_jd_does_not_start_workflow(self, client, auth_headers, no_autostart):
        """回归：未绑定 JD 时上传简历仍成功，但不应产生任何工作流运行记录。

        上传与匹配解耦——没有 JD 只是跑不了 AI 评估，不该阻断简历入库。
        """
        from src.models.database import WorkflowRun
        cid = _create_candidate(client, auth_headers, name="上传无JD")
        content = "候选人简历正文，用于验证上传链路，内容需超过最小长度限制。".encode("utf-8")
        r = client.post(f"/candidates/{cid}/upload-resume",
                        files={"file": ("r.txt", content, "text/plain")}, headers=auth_headers)
        assert r.status_code == 200, r.text
        with SessionLocal() as db:
            runs = db.query(WorkflowRun).filter(WorkflowRun.candidate_id == cid).count()
        assert runs == 0, "未绑定 JD 时不应创建工作流运行记录"


# ================================================================ 人工复核
class TestReviewQueue:
    def _make_pending(self, name):
        """构造一位处于待复核状态的候选人"""
        with SessionLocal() as db:
            c = Candidate(name=name, email=f"{name}_{os.urandom(3).hex()}@t.com",
                          status="pending_review")
            db.add(c)
            db.commit()
            db.refresh(c)
            db.add(TalentPool(candidate_id=c.id, status="active",
                              tags=["待人工复核", "文化契合边缘"], notes="待复核"))
            db.commit()
            return c.id

    def test_list_pending_reviews(self, client, auth_headers):
        cid = self._make_pending("待复核候选人")
        r = client.get("/reviews/", headers=auth_headers)
        assert r.status_code == 200
        ids = [x["candidate_id"] for x in r.json()]
        assert cid in ids

    def test_approve_rewrites_terminal_state_without_llm(self, client, auth_headers, monkeypatch):
        """裁决不重跑 LLM：直接改写终局，避免二次不可复现"""
        cid = self._make_pending("复核通过候选人")

        async def _boom(*a, **k):
            raise AssertionError("人工裁决不应触发 LLM 调用")
        monkeypatch.setattr("src.workflow.runner.start_workflow", _boom)

        r = client.post(f"/reviews/candidates/{cid}",
                        json={"decision": "approve", "note": "面试表现达标"}, headers=auth_headers)
        assert r.status_code == 200 and r.json()["status"] == "hired"
        with SessionLocal() as db:
            c = db.query(Candidate).filter(Candidate.id == cid).first()
            assert c.status == "hired"
            pool = db.query(TalentPool).filter(TalentPool.candidate_id == cid).first()
            assert "已录用" in pool.tags
            ev = db.query(Evaluation).filter(
                Evaluation.candidate_id == cid,
                Evaluation.dimension == "人工复核",
            ).first()
            assert ev is not None and ev.evaluator_id is not None

    def test_reject_rewrites_terminal_state(self, client, auth_headers):
        cid = self._make_pending("复核淘汰候选人")
        r = client.post(f"/reviews/candidates/{cid}",
                        json={"decision": "reject"}, headers=auth_headers)
        assert r.status_code == 200 and r.json()["status"] == "rejected"
        with SessionLocal() as db:
            pool = db.query(TalentPool).filter(TalentPool.candidate_id == cid).first()
            assert "已淘汰" in pool.tags

    def test_cannot_review_non_pending_candidate(self, client, auth_headers):
        cid = _create_candidate(client, auth_headers, name="非复核候选人")
        r = client.post(f"/reviews/candidates/{cid}", json={"decision": "approve"},
                        headers=auth_headers)
        assert r.status_code == 400 and "待复核" in r.json()["detail"]

    def test_invalid_decision_rejected(self, client, auth_headers):
        cid = self._make_pending("非法裁决候选人")
        r = client.post(f"/reviews/candidates/{cid}", json={"decision": "maybe"},
                        headers=auth_headers)
        assert r.status_code == 422

    def test_viewer_cannot_review(self, client):
        """只有 hr/admin 能裁决"""
        headers = login_headers(client, role="viewer")
        cid = self._make_pending("无权限候选人")
        r = client.post(f"/reviews/candidates/{cid}", json={"decision": "approve"}, headers=headers)
        assert r.status_code == 403