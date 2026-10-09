"""令牌有效期配置生效 + 令牌撤销（改密后旧令牌立即失效）。

回归背景两个：
1. `ACCESS_TOKEN_EXPIRE_MINUTES` 曾是**无效配置**——auth.py 里硬编码 30 分钟，
   .env 配 1440 也不生效。这类"配了没用"的问题不会报错，只会让人对系统做出错误判断。
2. JWT 无状态、签出去收不回来，原先没有改密接口也没有撤销机制：
   密码泄漏后唯一止血手段是换 SECRET_KEY（把所有人踢下线）。
"""
import calendar
from datetime import datetime, timedelta

import pytest
from jose import jwt

from src.api import auth
from tests.helpers import create_user, login_headers


def _decode(token: str) -> dict:
    return jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])


class TestTokenLifetime:
    def test_expiry_follows_settings(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 7)
        payload = _decode(auth.create_access_token(data={"sub": "u"}))
        span = payload["exp"] - payload["iat"]
        assert abs(span - 7 * 60) <= 2, f"应约 420 秒，实际 {span}"

    def test_explicit_expires_delta_wins(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 1440)
        payload = _decode(auth.create_access_token(
            data={"sub": "u"}, expires_delta=timedelta(minutes=3)))
        assert abs((payload["exp"] - payload["iat"]) - 180) <= 2

    def test_token_carries_iat(self):
        """没有 iat 就无法判断令牌是否在改密之前签发，撤销机制会失效"""
        payload = _decode(auth.create_access_token(data={"sub": "u"}))
        assert "iat" in payload


class TestPasswordChange:
    @pytest.fixture(autouse=True)
    def _reset(self):
        from src.services import login_guard
        login_guard.reset_for_test()
        yield
        login_guard.reset_for_test()

    def _setup(self, client, password="testpass123"):
        username = create_user(role="hr", password=password)
        r = client.post("/auth/login",
                        data={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return username, {"Authorization": f"Bearer {r.json()['access_token']}"}

    def test_change_password_succeeds(self, client):
        _, h = self._setup(client)
        r = client.post("/auth/users/me/password",
                        json={"old_password": "testpass123",
                              "new_password": "newpass456"},
                        headers=h)
        assert r.status_code == 200, r.text
        assert "access_token" in r.json()

    def test_wrong_old_password_rejected(self, client):
        _, h = self._setup(client)
        r = client.post("/auth/users/me/password",
                        json={"old_password": "not-my-password",
                              "new_password": "newpass456"},
                        headers=h)
        assert r.status_code == 400

    def test_same_password_rejected(self, client):
        _, h = self._setup(client)
        r = client.post("/auth/users/me/password",
                        json={"old_password": "testpass123",
                              "new_password": "testpass123"},
                        headers=h)
        assert r.status_code == 400

    def test_short_new_password_rejected(self, client):
        """长度校验在 schema 层，非法输入不进业务逻辑"""
        _, h = self._setup(client)
        r = client.post("/auth/users/me/password",
                        json={"old_password": "testpass123", "new_password": "short"},
                        headers=h)
        assert r.status_code == 422

    def test_unauthenticated_rejected(self, client):
        r = client.post("/auth/users/me/password",
                        json={"old_password": "x" * 10, "new_password": "y" * 10})
        assert r.status_code == 401

    def test_old_token_invalidated_after_change(self, client):
        """核心语义：改密后旧令牌立即失效（含被窃的那一份）"""
        username, old_h = self._setup(client)
        assert client.get("/auth/users/me", headers=old_h).status_code == 200

        r = client.post("/auth/users/me/password",
                        json={"old_password": "testpass123",
                              "new_password": "newpass456"},
                        headers=old_h)
        new_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        assert client.get("/auth/users/me", headers=old_h).status_code == 401, \
            "旧令牌必须失效"
        assert client.get("/auth/users/me", headers=new_h).status_code == 200, \
            "改密后返回的新令牌应继续可用"

    def test_new_password_works_for_fresh_login(self, client):
        username, h = self._setup(client)
        client.post("/auth/users/me/password",
                    json={"old_password": "testpass123", "new_password": "newpass456"},
                    headers=h)
        assert client.post("/auth/login",
                           data={"username": username, "password": "newpass456"}
                           ).status_code == 200
        assert client.post("/auth/login",
                           data={"username": username, "password": "testpass123"}
                           ).status_code == 401

    def test_other_users_tokens_unaffected(self, client):
        """撤销是 per-user 的，不能把别人踢下线"""
        victim_user, victim_h = self._setup(client)
        other_h = login_headers(client, role="hr")

        client.post("/auth/users/me/password",
                    json={"old_password": "testpass123", "new_password": "newpass456"},
                    headers=victim_h)

        assert client.get("/auth/users/me", headers=other_h).status_code == 200


class TestRevocationEdgeCases:
    """版本号比对的边界与老令牌处理。

    曾经用"签发时间 vs 撤销时间戳"实现，先后踩到两个坑：
    1. `datetime.utcnow().timestamp()` 会把 naive datetime 按本地时区解释
       （UTC+8 上偏 8 小时），导致改密后新签发的令牌被立即判为失效；
    2. 把撤销点截断到秒虽能救回新令牌，却让**同一秒内签发的旧令牌**逃过撤销。
    改用版本号后不含时钟，判定精确。
    """

    def _user(self, token_version=0):
        username = create_user(role="viewer")
        from src.models.database import SessionLocal, User
        with SessionLocal() as db:
            u = db.query(User).filter(User.username == username).first()
            u.token_version = token_version
            return u

    def test_matching_version_is_valid(self):
        assert auth._token_revoked({"tv": 3}, self._user(token_version=3)) is False

    def test_stale_version_is_revoked(self):
        assert auth._token_revoked({"tv": 2}, self._user(token_version=3)) is True

    def test_legacy_token_without_tv_counts_as_version_zero(self):
        """本次上线不会把所有人踢下线：老令牌按版本 0 处理，而默认版本就是 0"""
        assert auth._token_revoked({"sub": "u"}, self._user(token_version=0)) is False

    def test_legacy_token_revoked_after_password_change(self):
        """但用户一旦改密（版本 +1），老令牌立刻失效——不能给它们留后门"""
        assert auth._token_revoked({"sub": "u"}, self._user(token_version=1)) is True

    def test_lower_version_is_revoked(self):
        """版本只增不减；反向下也不可能出现，但真出现必须按失效处理"""
        assert auth._token_revoked({"tv": 1}, self._user(token_version=0)) is True
