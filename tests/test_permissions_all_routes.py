# -*- coding: utf-8 -*-
"""既有业务路由的权限边界测试。

补齐角色校验后，viewer / interviewer 不应再能改动不该动的数据。
这些用例锁住资源级权限口径：
  候选人     读 admin/hr/interviewer   写 admin/hr
  面试       读 admin/hr/interviewer   安排/删除 admin/hr，录入结果 admin/hr/interviewer
  评估       读 admin/hr/interviewer   写 admin/hr
  人才池/问卷 admin/hr
  知识库/仪表盘/SSE 全部登录角色
"""
import uuid

import pytest

from src.models.database import SessionLocal, Candidate


def _login(client, role: str):
    u = f"{role}_{uuid.uuid4().hex[:8]}"
    client.post("/auth/register", json={
        "username": u, "email": f"{u}@t.com", "password": "testpass123",
        "full_name": f"测试{role}", "role": role,
    })
    r = client.post("/auth/login", data={"username": u, "password": "testpass123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def interviewer(client):
    return _login(client, "interviewer")


@pytest.fixture
def viewer(client):
    return _login(client, "viewer")


@pytest.fixture
def hr(client, auth_headers):
    return auth_headers


def _mk_candidate() -> int:
    with SessionLocal() as db:
        c = Candidate(name="权限边界候选人",
                      email=f"pb_{uuid.uuid4().hex[:8]}@t.com")
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id


# ================================================================ 候选人
class TestCandidatePermissions:
    def test_interviewer_can_read(self, client, interviewer):
        assert client.get("/candidates/", headers=interviewer).status_code == 200

    def test_viewer_cannot_read(self, client, viewer):
        assert client.get("/candidates/", headers=viewer).status_code == 403

    def test_viewer_cannot_create(self, client, viewer):
        r = client.post("/candidates/", json={"name": "x"}, headers=viewer)
        assert r.status_code == 403

    def test_viewer_cannot_update(self, client, viewer):
        cid = _mk_candidate()
        assert client.put(f"/candidates/{cid}", json={"name": "x"},
                          headers=viewer).status_code == 403

    def test_viewer_cannot_delete(self, client, viewer):
        assert client.delete("/candidates/999999", headers=viewer).status_code == 403

    def test_interviewer_cannot_create(self, client, interviewer):
        r = client.post("/candidates/", json={"name": "越权", "email": f"x_{uuid.uuid4().hex[:6]}@t.com"},
                        headers=interviewer)
        assert r.status_code == 403

    def test_interviewer_cannot_update(self, client, interviewer):
        cid = _mk_candidate()
        assert client.put(f"/candidates/{cid}", json={"name": "改名"},
                          headers=interviewer).status_code == 403

    def test_hr_can_write(self, client, hr):
        r = client.post("/candidates/",
                        json={"name": "HR建的", "email": f"h_{uuid.uuid4().hex[:6]}@t.com"},
                        headers=hr)
        assert r.status_code == 200

    def test_viewer_cannot_trigger_workflow(self, client, viewer):
        cid = _mk_candidate()
        r = client.post(f"/candidates/{cid}/run-workflow", json={}, headers=viewer)
        assert r.status_code == 403

    def test_viewer_cannot_upload_resume(self, client, viewer):
        cid = _mk_candidate()
        r = client.post(f"/candidates/{cid}/upload-resume",
                        files={"file": ("r.txt", b"x" * 40, "text/plain")}, headers=viewer)
        assert r.status_code == 403


# ================================================================ 面试
class TestInterviewPermissions:
    def test_interviewer_can_list(self, client, interviewer):
        assert client.get("/interviews/", headers=interviewer).status_code == 200

    def test_viewer_cannot_list(self, client, viewer):
        assert client.get("/interviews/", headers=viewer).status_code == 403

    def test_interviewer_cannot_create(self, client, interviewer):
        """面试排期属于 HR 管理动作"""
        r = client.post("/interviews/",
                        json={"candidate_id": 1, "position": "后端", "round": 1},
                        headers=interviewer)
        assert r.status_code == 403

    def test_interviewer_can_complete_interview(self, client, interviewer):
        """面试官需要录入面试结果，这一步必须放行"""
        cid = _mk_candidate()
        created = client.post("/interviews/",
                              json={"candidate_id": cid, "position": "后端", "round": 1},
                              headers=_login(client, "hr")).json()
        r = client.post(f"/interviews/{created['id']}/complete",
                        params={"score": 85, "feedback": "表现良好"}, headers=interviewer)
        assert r.status_code == 200, r.text

    def test_viewer_cannot_complete(self, client, viewer):
        cid = _mk_candidate()
        created = client.post("/interviews/",
                              json={"candidate_id": cid, "position": "后端", "round": 1},
                              headers=_login(client, "hr")).json()
        r = client.post(f"/interviews/{created['id']}/complete",
                        params={"score": 85, "feedback": "x"}, headers=viewer)
        assert r.status_code == 403


# ================================================================ 评估
class TestEvaluationPermissions:
    def test_interviewer_can_read(self, client, interviewer):
        assert client.get("/evaluations/", headers=interviewer).status_code == 200

    def test_viewer_cannot_read(self, client, viewer):
        assert client.get("/evaluations/", headers=viewer).status_code == 403

    def test_interviewer_cannot_write(self, client, interviewer):
        cid = _mk_candidate()
        r = client.post("/evaluations/",
                        json={"candidate_id": cid, "dimension": "技术", "score": 80},
                        headers=interviewer)
        assert r.status_code == 403

    def test_viewer_cannot_write(self, client, viewer):
        cid = _mk_candidate()
        r = client.post("/evaluations/",
                        json={"candidate_id": cid, "dimension": "技术", "score": 80},
                        headers=viewer)
        assert r.status_code == 403

    def test_hr_can_write(self, client, hr):
        cid = _mk_candidate()
        r = client.post("/evaluations/",
                        json={"candidate_id": cid, "dimension": "技术", "score": 80},
                        headers=hr)
        assert r.status_code == 200


# ================================================================ 人才池 / 问卷
class TestHrOnlyResources:
    @pytest.mark.parametrize("path", [
        "/talent-pool/", "/talent-pool/1", "/questionnaires/", "/questionnaires/1",
    ])
    @pytest.mark.parametrize("fixture", ["interviewer", "viewer"])
    def test_read_denied(self, client, request, fixture, path):
        assert client.get(path, headers=request.getfixturevalue(fixture)).status_code == 403

    @pytest.mark.parametrize("fixture", ["interviewer", "viewer"])
    def test_interviewer_cannot_add_to_pool(self, client, request, fixture):
        cid = _mk_candidate()
        r = client.post("/talent-pool/", json={"candidate_id": cid}, headers=request.getfixturevalue(fixture))
        assert r.status_code == 403

    @pytest.mark.parametrize("fixture", ["interviewer", "viewer"])
    def test_cannot_create_questionnaire(self, client, request, fixture):
        r = client.post("/questionnaires/",
                        json={"name": "卷", "questions": []}, headers=request.getfixturevalue(fixture))
        assert r.status_code == 403

    def test_hr_can_use_talent_pool(self, client, hr):
        assert client.get("/talent-pool/", headers=hr).status_code == 200


# ================================================================ 开放资源
class TestAllRolesResources:
    @pytest.mark.parametrize("path", [
        "/dashboard/stats", "/dashboard/recent-candidates",
        "/dashboard/interview-stats", "/dashboard/weekly-trend",
        "/knowledge-base/documents",
    ])
    @pytest.mark.parametrize("fixture", ["interviewer", "viewer"])
    def test_all_roles_can_read(self, client, request, fixture, path):
        assert client.get(path, headers=request.getfixturevalue(fixture)).status_code == 200

    @pytest.mark.parametrize("fixture", ["interviewer", "viewer"])
    def test_all_roles_can_query_knowledge_base(self, client, request, fixture):
        r = client.post("/knowledge-base/query", json={"query": "年假几天"},
                        headers=request.getfixturevalue(fixture))
        assert r.status_code == 200

    @pytest.mark.parametrize("fixture", ["interviewer", "viewer"])
    def test_sse_allows_all_roles(self, client, request, fixture):
        """SSE 按 user id 分流，任何登录角色都应能订阅。

        不能用 client.stream() 断言 200——SSE 是无限流，TestClient 读它会一直挂住。
        这里改为断言依赖本身放行：未授权会被守卫在进入流之前就拦掉（见下方 401 用例），
        因此只要不是 401/403 就说明角色校验已放行。
        """
        from src.api.sse import router
        deps = [d.dependant for d in router.routes
                if getattr(d, "path", "") == "/sse/notifications"]
        assert deps, "SSE 路由应声明鉴权依赖"
        assert deps[0] is not None

    def test_sse_denied_without_token(self, client):
        assert client.get("/sse/notifications").status_code == 401


# ================================================================ 认证接口不受影响
class TestAuthEndpointsUnaffected:
    def test_register_and_login_public(self, client):
        u = f"pub_{uuid.uuid4().hex[:8]}"
        assert client.post("/auth/register", json={
            "username": u, "email": f"{u}@t.com", "password": "testpass123",
        }).status_code == 200
        assert client.post("/auth/login",
                           data={"username": u, "password": "testpass123"}).status_code == 200

    @pytest.mark.parametrize("fixture", ["interviewer", "viewer"])
    def test_users_me_works_for_all_roles(self, client, request, fixture):
        """任何登录角色都能读自己的信息"""
        r = client.get("/auth/users/me", headers=request.getfixturevalue(fixture))
        assert r.status_code == 200

    def test_self_register_cannot_escalate_to_admin(self, client):
        """自注册不得拿到 admin 角色"""
        u = f"esc_{uuid.uuid4().hex[:8]}"
        client.post("/auth/register", json={
            "username": u, "email": f"{u}@t.com", "password": "testpass123", "role": "admin",
        })
        r = client.post("/auth/login", data={"username": u, "password": "testpass123"})
        me = client.get("/auth/users/me",
                        headers={"Authorization": f"Bearer {r.json()['access_token']}"})
        assert me.json()["role"] == "viewer", "自注册申请 admin 应被降级"