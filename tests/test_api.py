"""API 冒烟与鉴权测试。"""
import os


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_register_normal_user(client):
    username = f"u_{os.urandom(3).hex()}"
    resp = client.post(
        "/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "pass12345",
            "role": "hr",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == username


def test_register_admin_role_falls_back_to_viewer(client):
    """Phase 2 修复：自注册禁止成为 admin，应回退为 viewer。"""
    username = f"admin_{os.urandom(3).hex()}"
    resp = client.post(
        "/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "pass12345",
            "role": "admin",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "viewer"


def test_login_returns_token(client):
    username = f"login_{os.urandom(3).hex()}"
    client.post(
        "/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "pass12345",
            "role": "hr",
        },
    )
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
