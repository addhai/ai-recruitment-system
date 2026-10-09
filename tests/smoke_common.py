"""冒烟脚本共用的认证助手。

冒烟脚本打的是**真实运行中的后端**（httpx 走 HTTP），所以不能像 pytest 那样
直接写库造账号，必须走接口。而 `/auth/register` 现在需要管理员权限
（它曾经完全无鉴权，任何人都能自助注册成 `hr`——而 `hr` 能读候选人简历 PII）。

这里先用种子管理员登录，再以管理员身份创建冒烟账号；账号已存在就直接登录。
"""
import os
import time

SMOKE_PASSWORD = "Smoke@12345"
ADMIN_USERNAME = os.environ.get("SMOKE_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "admin123")


def _login(c, username, password):
    return c.post("/auth/login", data={"username": username, "password": password})


def admin_headers(c) -> dict:
    """种子管理员账号的鉴权头。创建用户需要管理员权限（register 不再公开）。"""
    r = _login(c, ADMIN_USERNAME, ADMIN_PASSWORD)
    if r.status_code != 200:
        raise SystemExit(
            f"管理员登录失败（{r.status_code}）。请确认后端已启动且种子账号可用，"
            "或通过 SMOKE_ADMIN_USERNAME / SMOKE_ADMIN_PASSWORD 指定管理员。"
        )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def auth_token(c, username: str, role: str = "hr",
               password: str = SMOKE_PASSWORD,
               full_name: str = "冒烟测试员") -> str:
    """返回冒烟账号的 access_token；账号不存在则以管理员身份创建。"""
    r = _login(c, username, password)
    if r.status_code == 200:
        return r.json()["access_token"]

    admin = _login(c, ADMIN_USERNAME, ADMIN_PASSWORD)
    if admin.status_code != 200:
        raise SystemExit(
            f"需要管理员权限才能创建冒烟账号 {username}，但管理员登录失败"
            f"（{admin.status_code}）。请确认后端已启动且种子账号可用，"
            "或通过 SMOKE_ADMIN_USERNAME / SMOKE_ADMIN_PASSWORD 指定管理员。"
        )
    c.post("/auth/register",
           json={"username": username, "email": f"{username}@example.com",
                 "password": password, "full_name": full_name, "role": role},
           headers={"Authorization": f"Bearer {admin.json()['access_token']}"})

    r = _login(c, username, password)
    if r.status_code != 200:
        raise SystemExit(f"创建冒烟账号后仍无法登录：{r.status_code} {r.text[:200]}")
    return r.json()["access_token"]


def stamp() -> str:
    return time.strftime("%m%d%H%M%S")
