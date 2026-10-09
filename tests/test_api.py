"""API 冒烟与鉴权测试。"""
import os

from tests.helpers import admin_headers, create_user


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_register_requires_admin(client):
    """注册必须由管理员发起。

    回归背景：该端点曾经完全无鉴权，任何人都能自助注册成 `hr`，
    而 `hr` 能读候选人简历（PII）、做招聘裁决、看成本数据。
    """
    resp = client.post(
        "/auth/register",
        json={"username": f"u_{os.urandom(3).hex()}",
              "email": f"u_{os.urandom(3).hex()}@example.com",
              "password": "pass12345", "role": "hr"},
    )
    assert resp.status_code in (401, 403), "匿名注册必须被拒"


def test_register_rejects_non_admin_role_holder(client):
    """hr/viewer 也不能建号——建号是管理员职责"""
    for role in ("hr", "viewer", "interviewer"):
        with_h = _login_as(client, role)
        resp = client.post(
            "/auth/register",
            json={"username": f"x_{os.urandom(3).hex()}",
                  "email": f"x_{os.urandom(3).hex()}@example.com",
                  "password": "pass12345", "role": "viewer"},
            headers=with_h,
        )
        assert resp.status_code == 403, f"{role} 不应能建号"


def _login_as(client, role):
    """造一个指定角色账号并返回鉴权头（直接写库，不走注册接口）"""
    username = create_user(role=role)
    r = client.post("/auth/login",
                    data={"username": username, "password": "testpass123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_admin_can_register_any_valid_role(client):
    """管理员可以指派任意合法角色（含 admin）"""
    for role in ("admin", "hr", "interviewer", "viewer"):
        username = f"u_{role}_{os.urandom(3).hex()}"
        resp = client.post(
            "/auth/register",
            json={"username": username, "email": f"{username}@example.com",
                  "password": "pass12345", "role": role},
            headers=admin_headers(client),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == role


def test_admin_register_rejects_invalid_role(client):
    """非法角色直接拒绝，而不是悄悄降级——否则"填了没生效"很难查"""
    resp = client.post(
        "/auth/register",
        json={"username": f"u_{os.urandom(3).hex()}",
              "email": f"{os.urandom(3).hex()}@example.com",
              "password": "pass12345", "role": "root"},
        headers=admin_headers(client),
    )
    assert resp.status_code == 400


def test_login_returns_token(client):
    username = create_user(role="hr", password="pass12345")
    resp = client.post(
        "/auth/login", data={"username": username, "password": "pass12345"}
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_create_and_list_candidate(client, auth_headers):
    create = client.post(
        "/candidates",
        json={
            "name": "张三",
            "email": f"zhang_{os.urandom(3).hex()}@example.com",
            "position": "后端工程师",
        },
        headers=auth_headers,
    )
    assert create.status_code == 200
    cid = create.json()["id"]

    listing = client.get("/candidates", headers=auth_headers)
    assert listing.status_code == 200
    ids = [c["id"] for c in listing.json()]
    assert cid in ids


def test_protected_route_requires_auth(client):
    # 未带 token 访问受保护接口应被拒绝
    resp = client.get("/candidates")
    assert resp.status_code in (401, 403)
