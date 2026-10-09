"""共享冷却记录（`shared_cooldown`）：跨副本 / 跨重启的冷却期存储。

这里锁住模块自身的契约——mark/blocked_until 往返、到期释放、清理、行数上限，
以及最关键的**失败开放**：数据库不可用时读写都只告警、绝不外抛，否则一台数据库
抖动就会把唯一无鉴权的登录写路径整个打挂。
"""
import logging
from datetime import datetime, timedelta

import pytest

from src.services import shared_cooldown


class _Clock:
    def __init__(self, start: datetime = datetime(2026, 1, 1, 0, 0, 0)):
        self.utc = start

    def __call__(self) -> datetime:
        return self.utc

    def advance(self, seconds: float) -> None:
        self.utc += timedelta(seconds=seconds)


@pytest.fixture
def clk(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(shared_cooldown, "_now_utc", c)
    shared_cooldown.reset_for_test()
    yield c
    shared_cooldown.reset_for_test()


class TestRoundTrip:
    def test_mark_then_blocked(self, clk):
        assert shared_cooldown.blocked_until("t", "k") is None
        shared_cooldown.mark("t", "k", 60)
        assert shared_cooldown.blocked_until("t", "k") == \
            clk.utc + timedelta(seconds=60)

    def test_expiry_releases(self, clk):
        shared_cooldown.mark("t", "k", 60)
        clk.advance(61)
        assert shared_cooldown.blocked_until("t", "k") is None

    def test_boundary_still_blocked(self, clk):
        """到期前一刻仍应拦：判定条件是 until > now，边界处不能提前放行。"""
        shared_cooldown.mark("t", "k", 60)
        clk.advance(59)
        assert shared_cooldown.blocked_until("t", "k") is not None

    def test_repeated_mark_keeps_later_until(self, clk):
        """重复 mark 取更晚者：一次较短的 mark 不能提前解除已有冷却。"""
        shared_cooldown.mark("t", "k", 300)
        shared_cooldown.mark("t", "k", 60)
        assert shared_cooldown.blocked_until("t", "k") == \
            clk.utc + timedelta(seconds=300)

    def test_clear_removes(self, clk):
        shared_cooldown.mark("t", "k", 60)
        shared_cooldown.clear("t", "k")
        assert shared_cooldown.blocked_until("t", "k") is None

    def test_kind_isolation(self, clk):
        """kind 是命名空间的一部分：登录用户名维度不能误拦同名 IP。"""
        shared_cooldown.mark("a", "k", 60)
        assert shared_cooldown.blocked_until("b", "k") is None


class TestRowCap:
    def test_cap_evicts_earliest_until(self, clk, monkeypatch):
        """单 kind 超过上限时删 until 最早的——防止海量随机 key 把表撑大。"""
        monkeypatch.setattr(shared_cooldown, "_MAX_ROWS_PER_KIND", 3)
        for i in range(5):
            # seconds 递增 => until 递增，故 k0/k1 最先失效、应被淘汰
            shared_cooldown.mark("cap", f"k{i}", 100 + i * 10)
        assert shared_cooldown.blocked_until("cap", "k0") is None
        assert shared_cooldown.blocked_until("cap", "k1") is None
        for i in (2, 3, 4):
            assert shared_cooldown.blocked_until("cap", f"k{i}") is not None


class TestBestEffort:
    """数据库不可用时退化为"无共享记录"，绝不把异常抛给登录/调用路径。"""

    def test_mark_and_blocked_do_not_raise(self, clk, monkeypatch, caplog):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(shared_cooldown, "SessionLocal", _boom)
        with caplog.at_level(logging.WARNING,
                             logger="src.services.shared_cooldown"):
            shared_cooldown.mark("t", "k", 60)
            assert shared_cooldown.blocked_until("t", "k") is None
        assert any(r.levelno >= logging.WARNING for r in caplog.records), \
            "故障时应当只记 warning（可观测），而不是静默吞掉"

    def test_clear_does_not_raise(self, clk, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(shared_cooldown, "SessionLocal", _boom)
        shared_cooldown.clear("t", "k")   # 不抛
