"""登录限流：防爆破与防撞库。

用可注入的时钟驱动窗口逻辑，不依赖真实等待；也不 patch 全局 time.monotonic
（那会波及 asyncio/httpx 等所有使用者）。
"""
import pytest

from src.services import login_guard
from tests.helpers import create_user


class _Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(login_guard, "_now", c)
    login_guard.reset_for_test()
    yield c
    login_guard.reset_for_test()


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
        yield
        login_guard.reset_for_test()

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
