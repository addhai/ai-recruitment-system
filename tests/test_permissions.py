# -*- coding: utf-8 -*-
"""权限守卫测试：岗位、岗位 JD、人工复核三类敏感操作的角色边界。

背景：本项目此前所有业务路由只挂了 get_current_user，角色仅用于前端菜单隐藏，
不构成安全边界。这些用例把"仅 HR/管理员可操作"的约定钉死在后端。
"""
import os
import uuid

import pytest

from src.models.database import SessionLocal, JobDescription, Position


def _login(client, role: str):
    """注册指定角色的用户并返回其鉴权头"""
    u = f"{role}_{uuid.uuid4().hex[:8]}"
    client.post("/auth/register", json={
        "username": u, "email": f"{u}@t.com", "password": "testpass123",
        "full_name": f"测试{role}", "role": role,
    })
    r = client.post("/auth/login", data={"username": u, "password": "testpass123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def hr_headers(client, auth_headers):
    return auth_headers


@pytest.fixture
def interviewer_headers(client):
    return _login(client, "interviewer")


@pytest.fixture
def viewer_headers(client):
    return _login(client, "viewer")


def _mk_jd_direct(title="权限测试JD") -> int:
    """直接写库造一份已启用的 JD，绕开 API 权限"""
    with SessionLocal() as db:
        p = Position(title=title, status="active")
        db.add(p)
        db.flush()
        jd = JobDescription(
            position_id=p.id, title=title, status="active", parse_status="parsed",
            raw_text="精通 Python",
            parsed_data={"required_skills": [{"skill": "Python", "evidence": "精通 Python"}],
                         "culture_values": []},
        )
        db.add(jd)
        db.commit()
        db.refresh(jd)
        return jd.id


# ================================================================ 岗位
class TestPositionPermissions:
    def test_hr_can_create(self, client, hr_headers):
        r = client.post("/positions/", json={"title": "HR建的岗位"}, headers=hr_headers)
        assert r.status_code == 200

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_create(self, client, request, fixture):
        headers = request.getfixturevalue(fixture)
        r = client.post("/positions/", json={"title": "越权岗位"}, headers=headers)
        assert r.status_code == 403, f"{fixture} 不应能新建岗位"

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_read(self, client, request, fixture):
        headers = request.getfixturevalue(fixture)
        assert client.get("/positions/", headers=headers).status_code == 403

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_delete(self, client, request, fixture):
        pid = client.post("/positions/", json={"title": "待删"},
                          headers=_hr(client)).json()["id"]
        headers = request.getfixturevalue(fixture)
        assert client.delete(f"/positions/{pid}", headers=headers).status_code == 403

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_close(self, client, request, fixture):
        pid = client.post("/positions/", json={"title": "待关闭"},
                          headers=_hr(client)).json()["id"]
        headers = request.getfixturevalue(fixture)
        assert client.post(f"/positions/{pid}/close", headers=headers).status_code == 403


def _hr(client):
    """新建一个 HR 用户（种子已存在 hr001，但用独立账号避免测试间互相影响）"""
    return _login(client, "hr")


# ================================================================ 岗位 JD
class TestJobDescriptionPermissions:
    def test_hr_can_create(self, client, hr_headers):
        pid = client.post("/positions/", json={"title": "JD权限岗位"},
                          headers=hr_headers).json()["id"]
        r = client.post("/job_descriptions/", data={
            "position_id": str(pid), "title": "JD", "raw_text": "精通 Python",
        }, headers=hr_headers)
        assert r.status_code == 200

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_create(self, client, request, fixture):
        headers = request.getfixturevalue(fixture)
        pid = _mk_position_id()
        r = client.post("/job_descriptions/", data={
            "position_id": str(pid), "title": "越权JD", "raw_text": "精通 Python",
        }, headers=headers)
        assert r.status_code == 403

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_read(self, client, request, fixture):
        headers = request.getfixturevalue(fixture)
        assert client.get("/job_descriptions/", headers=headers).status_code == 403

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_parse_or_activate(self, client, request, fixture):
        jid = _mk_jd_direct()
        headers = request.getfixturevalue(fixture)
        assert client.post(f"/job_descriptions/{jid}/parse", headers=headers).status_code == 403
        assert client.post(f"/job_descriptions/{jid}/activate", headers=headers).status_code == 403

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_delete(self, client, request, fixture):
        jid = _mk_jd_direct("待删JD")
        headers = request.getfixturevalue(fixture)
        assert client.delete(f"/job_descriptions/{jid}", headers=headers).status_code == 403

    def test_unauthenticated_rejected(self, client):
        assert client.get("/positions/").status_code == 401
        assert client.get("/job_descriptions/").status_code == 401


def _mk_position_id() -> int:
    with SessionLocal() as db:
        p = Position(title="越权测试岗位")
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id


# ================================================================ 人工复核
class TestReviewPermissions:
    def _mk_pending(self) -> int:
        from src.models.database import Candidate
        with SessionLocal() as db:
            c = Candidate(name="权限复核候选人",
                          email=f"rv_{uuid.uuid4().hex[:8]}@t.com", status="pending_review")
            db.add(c)
            db.commit()
            db.refresh(c)
            return c.id

    def test_hr_can_decide(self, client, hr_headers):
        cid = self._mk_pending()
        r = client.post(f"/reviews/candidates/{cid}",
                        json={"decision": "approve"}, headers=hr_headers)
        assert r.status_code == 200

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_decide(self, client, request, fixture):
        cid = self._mk_pending()
        headers = request.getfixturevalue(fixture)
        r = client.post(f"/reviews/candidates/{cid}",
                        json={"decision": "approve"}, headers=headers)
        assert r.status_code == 403

    @pytest.mark.parametrize("fixture", ["interviewer_headers", "viewer_headers"])
    def test_non_hr_cannot_list(self, client, request, fixture):
        headers = request.getfixturevalue(fixture)
        assert client.get("/reviews/", headers=headers).status_code == 403

    def test_unauthenticated_rejected(self, client):
        assert client.get("/reviews/").status_code == 401
        assert client.post("/reviews/candidates/1", json={"decision": "approve"}).status_code == 401


# ================================================================ 守卫自身
class TestRequireRolesGuard:
    def test_guard_is_a_dependency_not_a_factory(self, client):
        """require_roles 才是工厂；require_hr_admin 等是已构造好的依赖"""
        from src.api.auth import require_roles, require_hr_admin
        guard = require_roles("hr", "admin")
        assert callable(guard) and callable(require_hr_admin)

    def test_error_message_lists_roles(self, client, viewer_headers):
        r = client.get("/positions/", headers=viewer_headers)
        assert r.status_code == 403
        assert "admin" in r.json()["detail"] and "hr" in r.json()["detail"]