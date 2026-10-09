"""LLM 供应商熔断。

「什么才算失败」比熔断本身更容易做错，所以这里把不该计入的几种情况单独钉住：
JSON 解析失败、预算耗尽都不算供应商故障——算进去会让"模型偶尔答得不规范"
或"今天预算用完了"直接熔断，把可用性做低。
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from langchain_core.messages import AIMessage

from src.services import circuit_breaker, llm_invoke, shared_cooldown
from src.models.database import SessionLocal, LLMCallLog, Cooldown


class _Clock:
    """同时驱动熔断的单调钟与共享记录的 UTC 墙钟。

    共享冷却要跨进程可比，用墙钟；本地状态机用单调钟。只推进一个会让另一个
    原地不动，从而把 test_half_open_after_cooldown_allows_one_probe 之类的用例
    变成假失败（本地已解锁、共享却仍拦着）。
    """

    def __init__(self, t=1_000_000.0):
        self.t = t
        self.utc = datetime(2026, 1, 1, 0, 0, 0)

    def __call__(self):
        return self.t

    def now_utc(self):
        return self.utc

    def advance(self, seconds):
        self.t += seconds
        self.utc += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(circuit_breaker, "_now", c)
    monkeypatch.setattr(shared_cooldown, "_now_utc", c.now_utc)
    circuit_breaker.reset_for_test()
    shared_cooldown.reset_for_test()
    yield c
    circuit_breaker.reset_for_test()
    shared_cooldown.reset_for_test()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setattr(circuit_breaker.settings, "LLM_CIRCUIT_BREAKER_ENABLED", True)
    monkeypatch.setattr(circuit_breaker.settings, "LLM_CIRCUIT_FAILURE_THRESHOLD", 3)
    monkeypatch.setattr(circuit_breaker.settings, "LLM_CIRCUIT_OPEN_SECONDS", 60)


class TestStateMachine:
    def test_closed_allows(self, clock, cfg):
        assert circuit_breaker.allow() is True
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.CLOSED

    def test_opens_after_threshold(self, clock, cfg):
        for _ in range(3):
            circuit_breaker.record_failure()
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.OPEN
        assert circuit_breaker.allow() is False

    def test_below_threshold_still_allows(self, clock, cfg):
        for _ in range(2):
            circuit_breaker.record_failure()
        assert circuit_breaker.allow() is True

    def test_success_resets_failure_count(self, clock, cfg):
        """偶发失败不该累积成熔断——成功一次就清零"""
        circuit_breaker.record_failure()
        circuit_breaker.record_failure()
        circuit_breaker.record_success()
        circuit_breaker.record_failure()
        circuit_breaker.record_failure()
        assert circuit_breaker.allow() is True, "计数应已清零，不该熔断"

    def test_half_open_after_cooldown_allows_one_probe(self, clock, cfg):
        for _ in range(3):
            circuit_breaker.record_failure()
        assert circuit_breaker.allow() is False

        clock.advance(61)
        assert circuit_breaker.allow() is True, "冷却到期应放一个探针"
        assert circuit_breaker.allow() is False, "探针只能有一个"
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.HALF_OPEN

    def test_probe_success_closes(self, clock, cfg):
        for _ in range(3):
            circuit_breaker.record_failure()
        clock.advance(61)
        assert circuit_breaker.allow() is True
        circuit_breaker.record_success()
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.CLOSED
        assert circuit_breaker.allow() is True

    def test_probe_failure_reopens_with_fresh_timer(self, clock, cfg):
        for _ in range(3):
            circuit_breaker.record_failure()
        clock.advance(61)
        circuit_breaker.allow()
        circuit_breaker.record_failure()

        assert circuit_breaker.snapshot()["state"] == circuit_breaker.OPEN
        assert circuit_breaker.allow() is False, "重新计时，不能再放探针"
        clock.advance(61)
        assert circuit_breaker.allow() is True

    def test_disabled_always_allows(self, clock, monkeypatch):
        monkeypatch.setattr(circuit_breaker.settings, "LLM_CIRCUIT_BREAKER_ENABLED", False)
        for _ in range(50):
            circuit_breaker.record_failure()
        assert circuit_breaker.allow() is True
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.CLOSED


# ================================================================ 与接入层联动
class _Boom:
    """调用必失败的假客户端。

    **不要定义 `input_schema`**：_invoke_raw 用它判断"是否已经是 Runnable"，
    定义了就会把裸对象直接拿去 `prompt | obj`，在**构图阶段**就抛
    unsupported type，异常发生在 try 之外（不计入熔断），测试也就测不到东西。
    不定义则会走 RunnableLambda 包装分支，异常从 invoke 里抛出——才是真实路径。
    """
    model_name = "boom"
    openai_api_base = "http://fake/v1"

    def invoke(self, _payload):
        raise RuntimeError("provider down")


class _BadJson:
    """调用成功但回答不是合法 JSON"""
    model_name = "badjson"
    openai_api_base = "http://fake/v1"

    def invoke(self, _payload):
        return AIMessage(content="这不是 JSON",
                         usage_metadata={"input_tokens": 1, "output_tokens": 1,
                                         "total_tokens": 2})


class TestIntegration:
    @pytest.fixture(autouse=True)
    def _clean(self):
        from src.services import budget
        circuit_breaker.reset_for_test()
        shared_cooldown.reset_for_test()
        asyncio.run(budget.reset_cache_for_test())
        yield
        circuit_breaker.reset_for_test()
        shared_cooldown.reset_for_test()
        with SessionLocal() as db:
            db.query(LLMCallLog).filter(
                LLMCallLog.call_site == "cb_probe").delete()
            db.commit()

    def _call(self, client):
        return asyncio.run(llm_invoke.invoke_json(
            "说点什么 {x}", {"x": "v"}, {"fallback": True}, "cb_probe",
            client=client))

    def test_request_failures_trip_the_breaker(self, monkeypatch):
        monkeypatch.setattr(circuit_breaker.settings, "LLM_CIRCUIT_FAILURE_THRESHOLD", 3)
        for _ in range(3):
            assert self._call(_Boom()) == {"fallback": True}
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.OPEN

    def test_open_circuit_fails_fast_with_distinct_status(self, monkeypatch):
        """熔断时不能再发请求，且埋点状态要与真实失败区分"""
        monkeypatch.setattr(circuit_breaker.settings, "LLM_CIRCUIT_FAILURE_THRESHOLD", 1)
        self._call(_Boom())
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.OPEN

        assert self._call(_Boom()) == {"fallback": True}
        with SessionLocal() as db:
            row = (db.query(LLMCallLog)
                   .filter(LLMCallLog.call_site == "cb_probe")
                   .order_by(LLMCallLog.id.desc()).first())
        assert row.status == "circuit_open", \
            "熔断是「我们主动不发」，不能混进 failed"

    def test_json_parse_failure_does_not_trip_breaker(self, monkeypatch):
        """模型答得不规范是输出质量问题，不是供应商故障。

        算进去的话，模型偶尔不听话就会熔断，可用性反而被做低。
        """
        monkeypatch.setattr(circuit_breaker.settings, "LLM_CIRCUIT_FAILURE_THRESHOLD", 1)
        for _ in range(3):
            assert self._call(_BadJson()) == {"fallback": True}
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.CLOSED, \
            "解析失败不应触发熔断"
        assert circuit_breaker.snapshot()["failures"] == 0

    def test_budget_exceeded_does_not_trip_breaker(self, monkeypatch):
        """预算耗尽是根本没发请求，与供应商健康无关"""
        from src.services import budget
        monkeypatch.setattr(circuit_breaker.settings, "LLM_CIRCUIT_FAILURE_THRESHOLD", 1)
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ENABLED", True)
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ACTION", "halt")
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_AMOUNT", 0.01)
        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 1.0)
        monkeypatch.setattr(budget.settings, "LLM_PEAK_MULTIPLIER", 1.0)
        monkeypatch.setattr(budget, "spent_in_window", _async_ret(99.0))
        asyncio.run(budget.reset_cache_for_test())

        with pytest.raises(budget.BudgetExceeded):
            self._call(_Boom())
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.CLOSED


class TestSharedCooldown:
    """共享熔断记录：让熔断跨副本、跨重启生效。

    本地状态机仍是判定主体；这些用例确认"另一个副本写下的记录"能在本地内存
    已清空时快速失败，同时不污染本地状态机（否则会误耗 half_open 探针名额）。
    """

    def test_other_replica_open_blocks_locally(self, clock, cfg):
        """另一个副本的共享熔断记录，使本地（内存已清空）allow() 返回 False。"""
        shared_cooldown.mark("llm_circuit", "default",
                             circuit_breaker.settings.LLM_CIRCUIT_OPEN_SECONDS)
        circuit_breaker.reset_for_test()
        assert circuit_breaker.allow() is False
        # 关键：共享拦截不该改写本地状态机（没消耗本地 half_open 探针名额）
        assert circuit_breaker.snapshot()["state"] == circuit_breaker.CLOSED

    def test_shared_record_expiry_restores_local_flow(self, clock, cfg):
        """共享冷却到期后，本地状态机照原逻辑走（CLOSED 放行）。"""
        shared_cooldown.mark("llm_circuit", "default", 60)
        circuit_breaker.reset_for_test()
        assert circuit_breaker.allow() is False
        clock.advance(61)
        assert circuit_breaker.allow() is True

    def test_disabled_breaker_ignores_shared_record(self, clock, cfg, monkeypatch):
        """关掉熔断（ENABLED=False）时共享记录不能影响 allow()，否则开关失效。"""
        shared_cooldown.mark("llm_circuit", "default", 60)
        circuit_breaker.reset_for_test()
        monkeypatch.setattr(circuit_breaker.settings,
                            "LLM_CIRCUIT_BREAKER_ENABLED", False)
        assert circuit_breaker.allow() is True

    def test_trigger_writes_shared_row(self, clock, cfg):
        """确认触发熔断确实在 cooldowns 表留下了一行。"""
        for _ in range(3):
            circuit_breaker.record_failure()
        with SessionLocal() as db:
            rows = (db.query(Cooldown)
                    .filter(Cooldown.kind == "llm_circuit").all())
        assert len(rows) == 1


def _async_ret(value):
    async def _inner(*a, **kw):
        return value
    return _inner
