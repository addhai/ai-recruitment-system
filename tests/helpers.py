"""测试共享助手：直接造账号，不走 HTTP 注册接口。

**为什么不能再用 `/auth/register` 造账号**：那个端点曾经完全无鉴权，测试为了
给每种角色批量造账号而依赖它，于是"公开可注册"被当成了预期行为断言进用例——
测试便利泄漏成了生产攻击面：任何人都能自助注册成 `hr`，而 `hr` 能读候选人
简历（PII）。现在 `register` 需要管理员权限，测试改为直接写库。
"""
import os
import uuid

from src.api.auth import get_password_hash
from src.models.database import SessionLocal, User

DEFAULT_PASSWORD = "testpass123"


def create_user(username: str = None, role: str = "hr",
                password: str = DEFAULT_PASSWORD,
                full_name: str = None, email: str = None) -> str:
    """直接写库创建用户，返回用户名。

    绕过 API 是刻意的：造测试数据的路径不该和被测的生产端点耦合——
    否则一旦端点收紧（比如这次），所有测试都会跟着碎，而且会诱导人
    为了"测试方便"把端点放松回去。
    """
    username = username or f"{role}_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(User(
            username=username,
            # 用 example.com 而不是 test.local —— EmailStr（email-validator）会把
            # .local / .test / .localhost 当作特殊用途域名拒掉。写库不经过校验，
            # 但这样造出来的账号一旦走任何经过校验的接口（如管理员改资料）就会 422，
            # 属于"测试数据本身不合法"的隐患。
            email=email or f"{username}@example.com",
            password_hash=get_password_hash(password),
            full_name=full_name or f"测试{role}",
            department="测试部",
            role=role,
        ))
        db.commit()
    return username


def login_headers(client, role: str = "hr", password: str = DEFAULT_PASSWORD,
                  username: str = None) -> dict:
    """造一个指定角色的账号并登录，返回鉴权头。"""
    username = create_user(username=username, role=role, password=password)
    resp = client.post("/auth/login",
                       data={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def admin_headers(client) -> dict:
    """种子管理员账号的鉴权头（init_db 会写入 admin/admin123）。"""
    resp = client.post("/auth/login",
                       data={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
