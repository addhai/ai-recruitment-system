# -*- coding: utf-8 -*-
"""岗位（Position）路由测试：岗位增删改、关闭招聘、删除保护。"""
import os

import pytest


def _create_candidate(client, headers, name="岗位测试候选人", **kw):
    payload = {"name": name, "email": f"{name}_{os.urandom(3).hex()}@t.com"}
    payload.update(kw)
    r = client.post("/candidates/", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture
def no_autostart(monkeypatch):
    monkeypatch.setattr("src.api.candidates.trigger_after_upload", lambda cid: None)


class TestPositionCrud:
    def test_create_and_list(self, client, auth_headers):
        r = client.post("/positions/", json={
            "title": "AI Agent 工程师", "department": "技术部",
            "location": "北京", "headcount": 2,
        }, headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "AI Agent 工程师" and body["status"] == "draft"
        assert body["jd_count"] == 0 and body["candidate_count"] == 0

        lst = client.get("/positions/", headers=auth_headers).json()
        assert any(x["id"] == body["id"] for x in lst)

    def test_update(self, client, auth_headers):
        pid = client.post("/positions/", json={"title": "旧岗位"}, headers=auth_headers).json()["id"]
        r = client.put(f"/positions/{pid}",
                       json={"title": "新岗位", "status": "active"}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["title"] == "新岗位" and r.json()["status"] == "active"

    def test_delete_position(self, client, auth_headers):
        pid = client.post("/positions/", json={"title": "待删岗位"}, headers=auth_headers).json()["id"]
        assert client.delete(f"/positions/{pid}", headers=auth_headers).status_code == 200
        assert client.get(f"/positions/{pid}", headers=auth_headers).status_code == 404

    def test_delete_blocked_when_has_jd(self, client, auth_headers):
        """岗位下有 JD 时不能直接删，避免 JD 变成孤儿数据"""
        pid = client.post("/positions/", json={"title": "有JD岗位"}, headers=auth_headers).json()["id"]
        client.post("/job_descriptions/",
                    data={"position_id": str(pid), "title": "JD", "raw_text": "精通 Python"},
                    headers=auth_headers)
        r = client.delete(f"/positions/{pid}", headers=auth_headers)
        assert r.status_code == 400 and "关闭招聘" in r.json()["detail"]

    def test_close_archives_position_and_its_jds(self, client, auth_headers):
        """关闭招聘应同时停用其下已启用的 JD"""
        pid = client.post("/positions/", json={"title": "要关闭的岗位"}, headers=auth_headers).json()["id"]
        jd = client.post("/job_descriptions/",
                         data={"position_id": str(pid), "title": "JD", "raw_text": "精通 Python"},
                         headers=auth_headers).json()
        # 直接置为 active 以验证关闭时会被一并归档
        from src.models.database import SessionLocal, JobDescription
        with SessionLocal() as db:
            row = db.query(JobDescription).filter(JobDescription.id == jd["id"]).first()
            row.status = "active"
            db.commit()

        r = client.post(f"/positions/{pid}/close", headers=auth_headers)
        assert r.status_code == 200 and r.json()["status"] == "archived"
        assert r.json()["active_jd_count"] == 0

    def test_archived_position_rejects_new_jd(self, client, auth_headers):
        pid = client.post("/positions/", json={"title": "已关闭岗位"}, headers=auth_headers).json()["id"]
        client.post(f"/positions/{pid}/close", headers=auth_headers)
        r = client.post("/job_descriptions/",
                        data={"position_id": str(pid), "title": "JD", "raw_text": "精通 Python"},
                        headers=auth_headers)
        assert r.status_code == 400 and "已关闭" in r.json()["detail"]

    def test_candidate_count_rolls_up_from_jds(self, client, auth_headers):
        """岗位的候选人统计要经其下 JD 汇总"""
        pid = client.post("/positions/", json={"title": "统计岗位"}, headers=auth_headers).json()["id"]
        jd = client.post("/job_descriptions/",
                         data={"position_id": str(pid), "title": "JD", "raw_text": "精通 Python"},
                         headers=auth_headers).json()
        _create_candidate(client, auth_headers, name="统计候选人", job_description_id=jd["id"])
        body = client.get(f"/positions/{pid}", headers=auth_headers).json()
        assert body["jd_count"] == 1 and body["candidate_count"] == 1

    def test_404_for_missing_position(self, client, auth_headers):
        assert client.get("/positions/999999", headers=auth_headers).status_code == 404
        assert client.put("/positions/999999", json={"title": "x"},
                          headers=auth_headers).status_code == 404
        assert client.delete("/positions/999999", headers=auth_headers).status_code == 404

    def test_unauthorized(self, client):
        assert client.get("/positions/").status_code == 401


class TestSkipRound3OnReview:
    """复核态跳过第 3 轮面试的开关"""
    def _mk_state(self, **kw):
        base = {"candidate_id": 0,
                "skill_match_score": 88, "experience_match_score": 85,
                "education_match_score": 90, "culture_match_score": 55,
                "questionnaire_score": 70,
                "interview_scores": [{"round": 1, "score": 85}, {"round": 2, "score": 88}],
                "needs_review": True,
                "review_detail": {"score": 55, "review_threshold": 50,
                                  "pass_threshold": 60, "degraded": False}}
        base.update(kw)
        return base

    def test_disabled_by_default_keeps_round3(self):
        from src.workflow.recruitment_graph import make_interview_check_with_r3_skip
        from src.config import settings
        assert settings.SKIP_ROUND3_ON_REVIEW is False, "开关默认应关闭，保持既有行为"
        assert make_interview_check_with_r3_skip(2)(self._mk_state()) == "pass"

    def test_enabled_with_review_flag_skips(self, monkeypatch):
        from src.workflow import recruitment_graph as rg
        monkeypatch.setattr(rg.settings, "SKIP_ROUND3_ON_REVIEW", True)
        assert rg.make_interview_check_with_r3_skip(2)(self._mk_state()) == "skip_r3"

    def test_enabled_without_review_flag_keeps_round3(self, monkeypatch):
        from src.workflow import recruitment_graph as rg
        monkeypatch.setattr(rg.settings, "SKIP_ROUND3_ON_REVIEW", True)
        state = self._mk_state(needs_review=False)
        assert rg.make_interview_check_with_r3_skip(2)(state) == "pass"

    def test_reject_still_rejects_when_skip_enabled(self, monkeypatch):
        """开关只影响"通过后是否跳过"，不能把不通过也放过去"""
        from src.workflow import recruitment_graph as rg
        monkeypatch.setattr(rg.settings, "SKIP_ROUND3_ON_REVIEW", True)
        state = self._mk_state(interview_scores=[{"round": 1, "score": 85},
                                                 {"round": 2, "score": 50}])
        assert rg.make_interview_check_with_r3_skip(2)(state) == "reject"

    def test_skip_does_not_unlock_hiring(self, monkeypatch):
        """跳过第 3 轮不等于放宽结论：终局仍锁档待人工复核"""
        import asyncio
        from src.workflow import recruitment_graph as rg
        from src.models.database import SessionLocal, Candidate
        monkeypatch.setattr(rg.settings, "SKIP_ROUND3_ON_REVIEW", True)

        async def _boom(*a, **k):
            raise AssertionError("复核态不应调用 LLM 决策")

        monkeypatch.setattr(rg, "_llm_json", _boom)

        import uuid
        with SessionLocal() as db:
            c = Candidate(name="跳过轮次测试",
                          email=f"skip_{uuid.uuid4().hex[:8]}@t.com")
            db.add(c)
            db.commit()
            db.refresh(c)
            cid = c.id

        out = asyncio.run(rg.generate_hiring_decision(self._mk_state(candidate_id=cid)))
        assert out["final_decision"] == "待人工复核"
        assert out["interview_r3_skipped"] is True
        assert out["final_recommendation"]["interview_r3_skipped"] is True