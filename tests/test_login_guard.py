"""登录限流：防爆破与防撞库。

用可注入的时钟驱动窗口逻辑，不依赖真实等待；也不 patch 全局 time.monotonic
（那会波及 asyncio/httpx 等所有使用者）。

共享冷却（多副本 / 重启）另用 UTC 墙钟，所以这里的假时钟同时驱动两个时间源：
只推进单调钟而墙钟不动，会让共享记录一直"未过期"，把 test_window_expiry_releases
这类用例变成假失败。
"""
from datetime import datetime, timedelta

import pytest

from src.models.database import Cooldown, SessionLocal
from src.services import login_guard, shared_cooldown
from tests.helpers import create_user


class _Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t
        self.utc = datetime(2026, 1, 1, 0, 0, 0)

    def __call__(self) -> float:
        return self.t

    def now_utc(self) -> datetime:
        return self.utc

    def advance(self, seconds: float) -> None:
        self.t += seconds
        self.utc += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(login_guard, "_now", c)
    monkeypatch.setattr(shared_cooldown, "_now_utc", c.now_utc)
    login_guard.reset_for_test()
    shared_cooldown.reset_for_test()
    yield c
    login_guard.reset_for_test()
    shared_cooldown.reset_for_test()


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setattr(login_guard.settings, "LOGIN_MAX_FAILED_PER_USER", 5)
    monkeypatch.setattr(login_guard.settings, "LOGIN_MAX_FAILED_PER_IP", 20)
    monkeypatch.setattr(login_guard.settings, "LOGIN_FAILURE_WINDOW_SECONDS", 900)
    monkeypatch.setattr(login_guard.settings, "LOGIN_LOCKOUT_SECONDS", 900)


class TestThreshold:
    def test_below_threshold_allowed(self, clock, limits):
        for _ in range(4):
            login_guard.record_failure("alice", "1.1.1.1")
        login_guard.check_allowed("alice", "1.1.1.1")   # 不抛

    def test_at_threshold_blocked(self, clock, limits):
        for _ in range(5):
            login_guard.record_failure("alice", "1.1.1.1")
        with pytest.raises(login_guard.LoginThrottled) as ei:
            login_guard.check_allowed("alice", "1.1.1.1")
        assert ei.value.retry_after > 0, "必须给出可重试时间，否则客户端只能盲试"

    def test_success_clears_user_counter(self, clock, limits):
        for _ in range(4):
            login_guard.record_failure("alice", "1.1.1.1")
        login_guard.record_success("alice")
        for _ in range(4):
            login_guard.record_failure("alice", "1.1.1.1")
        login_guard.check_allowed("alice", "1.1.1.1")   # 复位后仍可登录

    def test_window_expiry_releases(self, clock, limits):
        for _ in range(5):
            login_guard.record_failure("alice", "1.1.1.1")
        with pytest.raises(login_guard.LoginThrottled):
            login_guard.check_allowed("alice", "1.1.1.1")
        clock.advance(901)
        login_guard.check_allowed("alice", "1.1.1.1")

    def test_lockout_boundary_keeps_blocking(self, clock, limits):
        """锁定窗口内（< LOGIN_LOCKOUT_SECONDS）必须继续拦"""
        for _ in range(5):
            login_guard.record_failure("alice", "1.1.1.1")
        clock.advance(899)
        with pytest.raises(login_guard.LoginThrottled):
            login_guard.check_allowed("alice", "1.1.1.1")


class TestNoEnumerationOracle:
    def test_unknown_username_is_counted_too(self, clock, limits):
        """不存在的用户名也要计数。

        否则"存在才计数"就变成用户枚举探针：攻击者撞几次，被锁就说明账号存在。
        上限由调用方保证（login 端点无论用户是否存在都调 record_failure）。
        """
        for _ in range(5):
            login_guard.record_failure("does-not-exist", "1.1.1.1")
        with pytest.raises(login_guard.LoginThrottled):
            login_guard.check_allowed("does-not-exist", "1.1.1.1")


class TestPasswordSpraying:
    def test_ip_dimension_blocks_spraying(self, clock, limits):
        """撞库：每个用户名只试 3 次（低于用户名阈值），但同一 IP 累计会被拦"""
        for i in range(10):                       # 10 个用户名 × 3 次 = 30 > IP 阈值 20
            for _ in range(3):
                login_guard.record_failure(f"user{i}", "9.9.9.9")
        with pytest.raises(login_guard.LoginThrottled):
            login_guard.check_allowed("brand-new-user", "9.9.9.9")

    def test_success_does_not_whitewash_ip(self, clock, limits):
        """一次成功不应清掉 IP 维度的记录，否则攻击者中间夹一次自己的成功登录就能洗白"""
        for i in range(7):
            for _ in range(3):
                login_guard.record_failure(f"u{i}", "9.9.9.9")
        login_guard.record_success("attacker-own-account")
        with pytest.raises(login_guard.LoginThrottled):
            login_guard.check_allowed("whatever", "9.9.9.9")

    def test_other_ip_unaffected(self, clock, limits):
        for _ in range(10):
            login_guard.record_failure("alice", "9.9.9.9")
        login_guard.check_allowed("bob", "8.8.8.8")


class TestMemoryBound:
    def test_no_ip_key_when_ip_missing(self, clock, limits):
        """拿不到来源 IP 时只按用户名记，不能造出 'ip:None' 这种键"""
        login_guard.record_failure("alice", None)
        assert not any(k.startswith("ip:") for k in login_guard._failures)

    def test_key_count_is_capped(self, clock, limits, monkeypatch):
        monkeypatch.setattr(login_guard, "_MAX_KEYS", 50)
        for i in range(200):
            login_guard.record_failure(f"u{i}", None)
        assert len(login_guard._failures) <= 50, "海量随机用户名不能把内存撑爆"

    def test_disabled_when_limit_zero(self, clock, monkeypatch):
        monkeypatch.setattr(login_guard.settings, "LOGIN_MAX_FAILED_PER_USER", 0)
        monkeypatch.setattr(login_guard.settings, "LOGIN_MAX_FAILED_PER_IP", 0)
        for _ in range(50):
            login_guard.record_failure("alice", "1.1.1.1")
        login_guard.check_allowed("alice", "1.1.1.1")   # 阈值为 0 表示关闭


class TestLoginEndpoint:
    """经 API 的端到端行为"""

    @pytest.fixture(autouse=True)
    def _reset(self):
        login_guard.reset_for_test()
        shared_cooldown.reset_for_test()
        yield
        login_guard.reset_for_test()
        shared_cooldown.reset_for_test()

    def _fail_login(self, client, username):
        return client.post("/auth/login",
                           data={"username": username, "password": "wrong-password"})

    def test_repeated_failures_return_429(self, client):
        username = create_user(role="hr")
        for _ in range(5):
            assert self._fail_login(client, username).status_code == 401
        r = self._fail_login(client, username)
        assert r.status_code == 429
        assert "Retry-After" in r.headers, "429 必须带 Retry-After"

    def test_correct_password_blocked_during_lockout(self, client):
        """锁定期间即使密码正确也拒绝——否则限流形同虚设"""
        username = create_user(role="hr", password="testpass123")
        for _ in range(5):
            self._fail_login(client, username)
        r = client.post("/auth/login",
                        data={"username": username, "password": "testpass123"})
        assert r.status_code == 429

    def test_success_before_threshold_resets(self, client):
        username = create_user(role="hr", password="testpass123")
        for _ in range(3):
            self._fail_login(client, username)
        ok = client.post("/auth/login",
                         data={"username": username, "password": "testpass123"})
        assert ok.status_code == 200
        # 复位后再失败 3 次不应被锁
        for _ in range(3):
            assert self._fail_login(client, username).status_code == 401

    def test_unknown_user_also_throttled(self, client):
        """不存在的用户名同样会被限流，不泄漏账号是否存在"""
        for _ in range(6):
            last = self._fail_login(client, "no-such-user-xyz")
        assert last.status_code == 429

    def test_one_user_lockout_does_not_affect_another(self, client):
        victim = create_user(role="hr")
        other = create_user(role="hr", password="testpass123")
        for _ in range(6):
            self._fail_login(client, victim)
        ok = client.post("/auth/login",
                         data={"username": other, "password": "testpass123"})
        assert ok.status_code == 200


class TestSharedCooldown:
    """共享冷却记录：让锁定跨副本、跨进程重启生效。

    这些用例存在的理由：进程内存态被清空后（模拟"另一个副本"或"本进程重启"），
    仅凭共享记录仍必须拦截。没有它们，多副本部署下阈值会被放大成 副本数 × 阈值。
    """

    def test_other_replica_lock_is_honoured(self, clock, limits):
        """另一个副本已锁定：本地没有任何失败计数，只有共享记录，仍应被拦。"""
        login_guard.reset_for_test()
        shared_cooldown.mark("login_user", "alice",
                             login_guard.settings.LOGIN_LOCKOUT_SECONDS)
        with pytest.raises(login_guard.LoginThrottled):
            login_guard.check_allowed("alice", "1.1.1.1")

    def test_lock_survives_process_restart(self, clock, limits):
        """跨过阈值后清空内存态（= 进程重启），冷却期内仍应被拦。"""
        for _ in range(5):
            login_guard.record_failure("alice", "1.1.1.1")
        login_guard.reset_for_test()      # 内存计数清零，共享记录仍在
        with pytest.raises(login_guard.LoginThrottled):
            login_guard.check_allowed("alice", "1.1.1.1")

    def test_shared_cooldown_expiry_releases(self, clock, limits):
        """共享冷却到期后放行；这一步依赖假时钟同时推进墙钟与单调钟。"""
        for _ in range(5):
            login_guard.record_failure("alice", "1.1.1.1")
        login_guard.reset_for_test()
        clock.advance(901)
        login_guard.check_allowed("alice", "1.1.1.1")   # 不抛

    def test_threshold_writes_row_to_db(self, clock, limits):
        """确认跨过阈值确实在 cooldowns 表留下了一行，而不是只在内存里判定。"""
        for _ in range(5):
            login_guard.record_failure("alice", "1.1.1.1")
        with SessionLocal() as db:
            rows = (db.query(Cooldown)
                    .filter(Cooldown.kind == "login_user",
                            Cooldown.key == "alice").all())
        assert len(rows) == 1

    def test_success_clears_shared_user_not_ip(self, clock, limits):
        """record_success 只清共享用户名维度：否则一次成功就洗白整段 IP 攻击。"""
        shared_cooldown.mark("login_user", "alice", 900)
        shared_cooldown.mark("login_ip", "9.9.9.9", 900)
        login_guard.record_success("alice")
        assert shared_cooldown.blocked_until("login_user", "alice") is None
        assert shared_cooldown.blocked_until("login_ip", "9.9.9.9") is not None

    def test_disabled_limit_ignores_shared_record(self, clock, monkeypatch):
        """阈值配 0（关掉限流）时，不能因为别的副本残留的共享记录而拦截。"""
        monkeypatch.setattr(login_guard.settings, "LOGIN_MAX_FAILED_PER_USER", 0)
        monkeypatch.setattr(login_guard.settings, "LOGIN_MAX_FAILED_PER_IP", 0)
        shared_cooldown.mark("login_user", "alice", 900)
        login_guard.check_allowed("alice", "1.1.1.1")   # 不抛

