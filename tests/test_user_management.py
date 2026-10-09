"""用户管理接口：列表 / 修改 / 停用 / 重置密码。

重点在几条**边界**，它们都是"看起来能用但会造成不可挽回后果"的地方：
- 停用自己不亚于自锁
- 停用/降级最后一个管理员之后，没人能再管模型配置与用户，且无法自救
- 停用必须**立即**让已签发的令牌失效，而不是等它自然过期
- 硬删除会违反外键并让历史评估指向不存在的人，所以只提供停用
"""
import pytest

from src.api.pagination import MAX_PAGE_SIZE
from src.models.database import SessionLocal, User
from tests.helpers import admin_headers, create_user, login_headers


def _h(client, role="hr", **kw):
    return login_headers(client, role=role, **kw)


def _all_users(client, headers) -> list:
    """取全部用户，并确认**确实取全了**。

    /auth/users 有分页且不返回总数，所以"列表里没有我找的人"既可能是真没有，
    也可能是被分页截断了——后者会伪装成用例失败，根因很难看出
    （实测：分页上限默认 100，而测试库是会话级共享的，跑全量时用户数远超 100）。
    这里把"没取全"变成一句明确的断言，而不是让下面各条用例各自莫名其妙地失败。
    """
    rows = client.get("/auth/users", params={"limit": MAX_PAGE_SIZE},
                      headers=headers).json()
    assert len(rows) < MAX_PAGE_SIZE, (
        f"库中用户数已达到分页上限 {MAX_PAGE_SIZE}，本用例看到的不一定是全部。"
        "需要先提高上限或改用分页遍历")
    return rows


def _uid(username: str) -> int:
    """按用户名直接查 id。

    **不要改成翻 /auth/users 列表去找**：该接口有分页（默认 100），
    而测试库是整个会话共享的，跑全量时用户数远超 100，
    新建用户落在后页，测试会随机找不到人。
    """
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).first()
        assert user is not None, f"用户 {username} 不存在"
        return user.id


@pytest.fixture(autouse=True)
def restore_user_state():
    """本文件会改角色与启用状态，而测试库是会话级共享的。

    不恢复的话一次"降级最后一个管理员"就会污染后续所有用例——
    实测：种子 admin 被降成 hr 之后，后面依赖 admin 的用例全部级联失败
    （表现为拿 403 错误体当列表遍历的 TypeError，根因很难一眼看出）。
    """
    with SessionLocal() as db:
        snap = {u.id: (u.role, u.is_active, u.token_version)
                for u in db.query(User).all()}
    yield
    with SessionLocal() as db:
        for uid, (role, active, tv) in snap.items():
            u = db.query(User).filter(User.id == uid).first()
            if u is not None:
                u.role, u.is_active, u.token_version = role, active, tv
        db.commit()


@pytest.fixture
def only_seed_admin_is_admin():
    """让种子 admin 成为唯一可用管理员。

    "最后一个管理员"这类边界必须先把局面做出来，否则别的测试文件留下的
    额外管理员会让边界根本不触发（实测就是这样失败过一次）。
    复原交给 restore_user_state。
    """
    with SessionLocal() as db:
        seed = db.query(User).filter(User.username == "admin").first()
        for u in (db.query(User)
                  .filter(User.role == "admin", User.id != seed.id).all()):
            u.role = "hr"
        db.commit()
    yield


class TestListUsers:
    def test_requires_admin(self, client):
        for role in ("hr", "interviewer", "viewer"):
            assert client.get("/auth/users", headers=_h(client, role)).status_code == 403
        assert client.get("/auth/users").status_code == 401

    def test_admin_sees_users(self, client):
        username = create_user(role="hr")
        rows = _all_users(client, admin_headers(client))
        assert any(r["username"] == username for r in rows)
        assert all("is_active" in r for r in rows)

    def test_includes_inactive_users(self, client):
        """停用的账号也要列出来，否则管理员会以为操作没生效而重复建号"""
        h = admin_headers(client)
        uid = _uid(create_user(role="hr"))
        client.put(f"/auth/users/{uid}", json={"is_active": False}, headers=h)

        row = next(r for r in _all_users(client, h) if r["id"] == uid)
        assert row["is_active"] is False

    def test_pagination_accepted(self, client):
        h = admin_headers(client)
        r = client.get("/auth/users", params={"limit": 1}, headers=h)
        assert r.status_code == 200 and len(r.json()) <= 1
        assert client.get("/auth/users", params={"limit": 99999},
                          headers=h).status_code == 422


class TestUpdateUser:
    def _target(self, client, h, role="hr"):
        return _uid(create_user(role=role))

    def test_requires_admin(self, client):
        h = admin_headers(client)
        uid = self._target(client, h)
        for role in ("hr", "interviewer", "viewer"):
            assert client.put(f"/auth/users/{uid}", json={"full_name": "x"},
                              headers=_h(client, role)).status_code == 403

    def test_update_profile_fields(self, client):
        h = admin_headers(client)
        uid = self._target(client, h)
        r = client.put(f"/auth/users/{uid}",
                       json={"full_name": "新名字", "department": "新部门"},
                       headers=h)
        assert r.status_code == 200
        assert r.json()["full_name"] == "新名字"
        assert r.json()["department"] == "新部门"

    def test_update_role(self, client):
        h = admin_headers(client)
        uid = self._target(client, h)
        assert client.put(f"/auth/users/{uid}", json={"role": "interviewer"},
                          headers=h).json()["role"] == "interviewer"

    def test_invalid_role_rejected(self, client):
        h = admin_headers(client)
        uid = self._target(client, h)
        assert client.put(f"/auth/users/{uid}", json={"role": "root"},
                          headers=h).status_code == 400

    def test_username_not_changeable(self, client):
        """用户名是 JWT 的 sub，改名会让已签发令牌与历史记录对不上"""
        h = admin_headers(client)
        uid = self._target(client, h)
        before = next(r["username"] for r in _all_users(client, h) if r["id"] == uid)
        client.put(f"/auth/users/{uid}", json={"username": "renamed"}, headers=h)
        after = next(r["username"] for r in _all_users(client, h) if r["id"] == uid)
        assert after == before

    def test_email_clash_rejected(self, client):
        h = admin_headers(client)
        a = self._target(client, h)
        b = self._target(client, h)
        email_b = next(r["email"] for r in _all_users(client, h) if r["id"] == b)
        assert client.put(f"/auth/users/{a}", json={"email": email_b},
                          headers=h).status_code == 400

    def test_unknown_user_404(self, client):
        assert client.put("/auth/users/999999", json={"full_name": "x"},
                          headers=admin_headers(client)).status_code == 404


class TestDeactivateGuards:
    def test_cannot_deactivate_self(self, client):
        """停用自己等同于自锁，且没有别的人能给你恢复（除非有另一个管理员）"""
        h = admin_headers(client)
        me = client.get("/auth/users/me", headers=h).json()
        r = client.put(f"/auth/users/{me['id']}", json={"is_active": False}, headers=h)
        assert r.status_code == 400
        assert "当前登录" in r.json()["detail"]

    def test_admin_can_deactivate_another_admin(self, client):
        """有两个管理员时停用其中一个是允许的"""
        h = admin_headers(client)
        uid = _uid(create_user(role="admin"))
        assert client.put(f"/auth/users/{uid}", json={"is_active": False},
                          headers=h).status_code == 200

    def test_cannot_demote_last_active_admin(self, client, only_seed_admin_is_admin):
        """把自己从最后一个管理员降级 → 没人能再进用户管理与模型配置，且无法自救"""
        h = admin_headers(client)
        me = client.get("/auth/users/me", headers=h).json()
        r = client.put(f"/auth/users/{me['id']}", json={"role": "hr"}, headers=h)
        assert r.status_code == 400
        assert "最后一个" in r.json()["detail"]

    def test_can_demote_admin_when_another_exists(self, client, only_seed_admin_is_admin):
        """还有一个管理员时，降级自己是允许的（有退路）"""
        h = admin_headers(client)
        other = _uid(create_user(role="admin"))
        me = client.get("/auth/users/me", headers=h).json()
        assert client.put(f"/auth/users/{me['id']}", json={"role": "hr"},
                          headers=h).status_code == 200
        assert other  # 另一个管理员仍在

    def test_another_admin_can_manage_a_demoted_admin(self, client, only_seed_admin_is_admin):
        """降级后该账号仍可被其他管理员管理（不是变成幽灵账号）"""
        h = admin_headers(client)
        uid = _uid(create_user(role="admin"))
        client.put(f"/auth/users/{uid}", json={"role": "hr"}, headers=h)
        assert client.put(f"/auth/users/{uid}", json={"role": "viewer"},
                          headers=h).status_code == 200

    # 注：「停用最后一个可用管理员」的那条分支在 API 上**不可达**，因此不写用例：
    # 调用者必须是管理员，而"停用自己"的检查在其之前已经拦住——
    # 目标等于调用者时命中自锁检查；目标不等于调用者时，库里必然还有调用者
    # 这个可用管理员，`_other_active_admins(target) == 0` 永远不成立。
    # 该分支作为防御性代码保留（万一将来放宽自锁检查），但不要以为它被测到了。


class TestDeactivationTakesEffectImmediately:
    def test_login_rejected_after_deactivation(self, client):
        h = admin_headers(client)
        username = create_user(role="hr", password="testpass123")
        uid = _uid(username)

        assert client.post("/auth/login",
                           data={"username": username, "password": "testpass123"}
                           ).status_code == 200

        client.put(f"/auth/users/{uid}", json={"is_active": False}, headers=h)

        # 与"密码错"同样的 401：否则能靠错误差异判断"账号存在但被停用"
        r = client.post("/auth/login",
                        data={"username": username, "password": "testpass123"})
        assert r.status_code == 401
        assert r.json()["detail"] == "Incorrect username or password"

    def test_existing_token_revoked_immediately(self, client):
        """停用不能让旧令牌继续用到自然过期——那等于停用没生效"""
        h = admin_headers(client)
        victim = _h(client, role="hr")
        uid = client.get("/auth/users/me", headers=victim).json()["id"]
        assert client.get("/auth/users/me", headers=victim).status_code == 200

        client.put(f"/auth/users/{uid}", json={"is_active": False}, headers=h)

        assert client.get("/auth/users/me", headers=victim).status_code == 401

    def test_reenable_restores_access(self, client):
        h = admin_headers(client)
        username = create_user(role="hr", password="testpass123")
        uid = _uid(username)
        client.put(f"/auth/users/{uid}", json={"is_active": False}, headers=h)
        client.put(f"/auth/users/{uid}", json={"is_active": True}, headers=h)
        assert client.post("/auth/login",
                           data={"username": username, "password": "testpass123"}
                           ).status_code == 200


class TestAdminPasswordReset:
    def test_requires_admin(self, client):
        uid = client.get("/auth/users/me", headers=_h(client, role="hr")).json()["id"]
        assert client.post(f"/auth/users/{uid}/password",
                           json={"new_password": "brandnew123"},
                           headers=_h(client, role="hr")).status_code == 403

    def test_reset_lets_user_login_with_new_password(self, client):
        h = admin_headers(client)
        username = create_user(role="hr", password="testpass123")
        uid = _uid(username)

        r = client.post(f"/auth/users/{uid}/password",
                        json={"new_password": "brandnew123"}, headers=h)
        assert r.status_code == 200

        assert client.post("/auth/login",
                           data={"username": username, "password": "brandnew123"}
                           ).status_code == 200
        assert client.post("/auth/login",
                           data={"username": username, "password": "testpass123"}
                           ).status_code == 401

    def test_reset_revokes_existing_tokens(self, client):
        """重置密码意味着此前所有会话失效，否则被盗账号改不掉"""
        h = admin_headers(client)
        victim_h = _h(client, role="hr")
        uid = client.get("/auth/users/me", headers=victim_h).json()["id"]
        assert client.get("/auth/users/me", headers=victim_h).status_code == 200

        client.post(f"/auth/users/{uid}/password",
                    json={"new_password": "brandnew123"}, headers=h)

        assert client.get("/auth/users/me", headers=victim_h).status_code == 401

    def test_short_password_rejected(self, client):
        h = admin_headers(client)
        uid = client.get("/auth/users/me", headers=_h(client, role="hr")).json()["id"]
        assert client.post(f"/auth/users/{uid}/password",
                           json={"new_password": "short"}, headers=h).status_code == 422
