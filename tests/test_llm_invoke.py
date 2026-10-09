# -*- coding: utf-8 -*-
"""LLM 埋点与成本记账单测：token 提取、成本折算、降级标记、预算拦截。

不发起真实 LLM 调用：mock 掉 get_llm() 返回一个可 chain 化的假客户端。
"""
import asyncio
import uuid

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from src.services import llm_invoke, budget
from src.models.database import SessionLocal, LLMCallLog


def _msg_json(usage_metadata=None, response_metadata=None, content='{"score": 88}'):
    """构造真实 AIMessage——输出解析器要求真实消息类型。

    注意 usage_metadata 是 langchain 的类型化字段，必须含 total_tokens，
    这也印证了埋点取值要以 langchain 的实际契约为准。
    """
    um = None
    if usage_metadata is not None:
        um = {**usage_metadata,
              "total_tokens": usage_metadata.get("input_tokens", 0)
              + usage_metadata.get("output_tokens", 0)}
    return AIMessage(content=content, usage_metadata=um,
                     response_metadata=response_metadata or {})


class _FakeLLM:
    """可 chain 化的假客户端。

    `prompt | llm` 要求 llm 是 Runnable，所以用 RunnableLambda 包一层，
    而不是直接暴露一个自定义对象（那样组链会报 unsupported type）。
    """
    model_name = "fake-model"
    openai_api_base = "http://fake/v1"

    def __init__(self, msg=None, raises=None):
        self._msg = msg
        self._raises = raises

    def invoke(self, _payload):
        if self._raises:
            raise self._raises
        return self._msg if self._msg is not None else _msg_json()

    def as_runnable(self):
        return RunnableLambda(self.invoke)


@pytest.fixture(autouse=True)
def _clean_logs():
    yield
    with SessionLocal() as db:
        db.query(LLMCallLog).delete()
        db.commit()


def _latest_log():
    with SessionLocal() as db:
        return db.query(LLMCallLog).order_by(LLMCallLog.id.desc()).first()


# ================================================================ token 提取
class TestUsageExtraction:
    def test_from_usage_metadata(self):
        msg = _msg_json(usage_metadata={"input_tokens": 1200, "output_tokens": 340})
        assert llm_invoke._extract_usage(msg) == (1200, 340)

    def test_from_response_metadata_legacy_keys(self):
        msg = _msg_json(response_metadata={"token_usage": {"prompt_tokens": 800,
                                                            "completion_tokens": 200}})
        assert llm_invoke._extract_usage(msg) == (800, 200)

    def test_from_response_metadata_modern_keys(self):
        msg = _msg_json(response_metadata={"usage": {"input_tokens": 500,
                                                      "output_tokens": 120}})
        assert llm_invoke._extract_usage(msg) == (500, 120)

    def test_missing_everywhere_returns_none(self):
        assert llm_invoke._extract_usage(_msg_json()) == (None, None)


# ================================================================ 成本折算
class TestCostComputation:
    @pytest.fixture(autouse=True)
    def _flat_pricing(self, monkeypatch):
        """固定为不分时段。

        否则测试结果会随墙上时钟变化：LLM_PEAK_MULTIPLIER > 1 且当前落在
        高峰时段（北京时间周一至周五 9-12 / 14-18）时成本会翻倍，
        断言就变成了"跑测试的时刻对不对"。分时计价另有专门用例覆盖。
        """
        monkeypatch.setattr(budget.settings, "LLM_PEAK_MULTIPLIER", 1.0)
        yield

    def test_zero_pricing_yields_zero(self, monkeypatch):
        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 0.0)
        monkeypatch.setattr(budget.settings, "LLM_OUTPUT_PRICE_PER_MILLION", 0.0)
        assert budget.cost_of_call(100000, 100000) == 0.0

    def test_pricing_applied(self, monkeypatch):
        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 2.0)
        monkeypatch.setattr(budget.settings, "LLM_OUTPUT_PRICE_PER_MILLION", 8.0)
        # 1M input * 2 + 0.5M output * 8 = 2 + 4
        assert budget.cost_of_call(1_000_000, 500_000) == pytest.approx(6.0)

    def test_zero_tokens_no_error(self, monkeypatch):
        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 2.0)
        assert budget.cost_of_call(None, None) == 0.0

    def test_peak_multiplier_doubles_cost(self, monkeypatch):
        """高峰时段单价翻倍——用注入时间断言，不依赖跑测试的真实时刻"""
        from datetime import datetime, timezone, timedelta

        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 2.0)
        monkeypatch.setattr(budget.settings, "LLM_OUTPUT_PRICE_PER_MILLION", 8.0)
        monkeypatch.setattr(budget.settings, "LLM_PEAK_MULTIPLIER", 2.0)

        cn = timezone(timedelta(hours=8))
        # 2026-10-09 是周五：10:00 北京时间 = 高峰
        peak = datetime(2026, 10, 9, 10, 0, tzinfo=cn)
        # 03:00 北京时间 = 空闲
        idle = datetime(2026, 10, 9, 3, 0, tzinfo=cn)
        # 周六 10:00 = 全天空闲
        weekend = datetime(2026, 10, 10, 10, 0, tzinfo=cn)

        assert budget.cost_of_call(1_000_000, 0, now=peak) == pytest.approx(4.0)
        assert budget.cost_of_call(1_000_000, 0, now=idle) == pytest.approx(2.0)
        assert budget.cost_of_call(1_000_000, 0, now=weekend) == pytest.approx(2.0)


# ================================================================ 预算
class TestBudget:
    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ENABLED", True)
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ACTION", "halt")
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_AMOUNT", 5.0)
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_USD", None)
        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 2.0)
        yield

    def test_under_limit_passes(self, monkeypatch):
        monkeypatch.setattr(budget, "spent_in_window", _async_ret(1.0))
        asyncio.run(budget.check_budget())  # 不抛

    def test_over_limit_raises(self, monkeypatch):
        monkeypatch.setattr(budget, "spent_in_window", _async_ret(6.0))
        with pytest.raises(budget.BudgetExceeded):
            asyncio.run(budget.check_budget())

    def test_action_warn_does_not_block(self, monkeypatch):
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ACTION", "warn")
        monkeypatch.setattr(budget, "spent_in_window", _async_ret(99.0))
        asyncio.run(budget.check_budget())  # 不抛

    def test_pricing_unconfigured_skips_check(self, monkeypatch):
        """单价没配时所有成本记 0，不能因为忘配价格就把业务卡死"""
        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 0.0)
        monkeypatch.setattr(budget.settings, "LLM_OUTPUT_PRICE_PER_MILLION", 0.0)
        monkeypatch.setattr(budget, "spent_in_window", _async_ret(99.0))
        asyncio.run(budget.check_budget())  # 不抛

    def test_disabled_skips_check(self, monkeypatch):
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ENABLED", False)
        monkeypatch.setattr(budget, "spent_in_window", _async_ret(99.0))
        asyncio.run(budget.check_budget())

    def test_zero_limit_skips(self, monkeypatch):
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_AMOUNT", 0.0)
        monkeypatch.setattr(budget, "spent_in_window", _async_ret(99.0))
        asyncio.run(budget.check_budget())

    def test_add_spend_accumulates(self):
        asyncio.run(budget.reset_cache_for_test())
        asyncio.run(budget.add_spend(0.25))
        asyncio.run(budget.add_spend(0.75))
        assert budget._spent == pytest.approx(1.0)

    def test_budget_limit_reads_amount_field(self, monkeypatch):
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_USD", None)
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_AMOUNT", 12.5)
        assert budget.budget_limit() == pytest.approx(12.5)

    def test_budget_limit_legacy_alias_wins(self, monkeypatch):
        """旧字段名被显式设置时优先，避免老部署升级后静默丢掉上限保护"""
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_AMOUNT", 12.5)
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_USD", 3.0)
        assert budget.budget_limit() == pytest.approx(3.0)

    def test_over_limit_message_uses_configured_currency(self, monkeypatch):
        """上限金额的币种必须跟计价币种一致，不能写死 $ 谎报金额"""
        monkeypatch.setattr(budget.settings, "LLM_PRICE_CURRENCY", "CNY")
        monkeypatch.setattr(budget, "spent_in_window", _async_ret(6.0))
        with pytest.raises(budget.BudgetExceeded) as ei:
            asyncio.run(budget.check_budget())
        msg = str(ei.value)
        assert "CNY" in msg
        assert "$" not in msg

    def test_spent_in_window_excludes_foreign_currency(self, monkeypatch):
        """窗口累加只认当前币种：跨币种金额相加会让上限形同虚设"""
        from datetime import datetime
        from src.models.database import SessionLocal, LLMCallLog

        monkeypatch.setattr(budget.settings, "LLM_PRICE_CURRENCY", "CNY")
        try:
            with SessionLocal() as db:
                db.add(LLMCallLog(created_at=datetime.utcnow(),
                                  call_site="fx_probe", model="m", cost=9.0,
                                  currency="USD", latency_ms=1, status="ok",
                                  degraded=False, usage_missing=False))
                db.add(LLMCallLog(created_at=datetime.utcnow(),
                                  call_site="fx_probe", model="m", cost=0.25,
                                  currency="CNY", latency_ms=1, status="ok",
                                  degraded=False, usage_missing=False))
                db.commit()

            asyncio.run(budget.reset_cache_for_test())
            spent = asyncio.run(budget.spent_in_window())
            # 9.0 USD 不能被算成 9.0 CNY
            assert spent == pytest.approx(0.25)
        finally:
            with SessionLocal() as db:
                db.query(LLMCallLog).filter(
                    LLMCallLog.call_site == "fx_probe").delete()
                db.commit()
            asyncio.run(budget.reset_cache_for_test())

    def test_add_spend_resets_accumulator_on_currency_change(self, monkeypatch):
        """币种变了，旧的进程内累计值不能留（¥ 与 $ 不可相加）"""
        monkeypatch.setattr(budget.settings, "LLM_PRICE_CURRENCY", "CNY")
        asyncio.run(budget.reset_cache_for_test())
        asyncio.run(budget.add_spend(3.0))
        assert budget._spent == pytest.approx(3.0)
        assert budget._spent_currency == "CNY"

        monkeypatch.setattr(budget.settings, "LLM_PRICE_CURRENCY", "USD")
        asyncio.run(budget.add_spend(1.0))
        assert budget._spent == pytest.approx(1.0)      # 3.0 CNY 已被丢弃
        assert budget._spent_currency == "USD"
        assert budget._never_read_db is True            # 强制下次回读
        asyncio.run(budget.reset_cache_for_test())


# ================================================================ 埋点落库
class TestInvokeJsonInstrumentation:
    @pytest.fixture(autouse=True)
    def _no_budget(self, monkeypatch):
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ENABLED", False)
        asyncio.run(budget.reset_cache_for_test())
        yield

    def _run(self, fake_llm, **kw):
        monkey = kw.pop("monkeypatch", None)
        if monkey:
            monkey.setattr(llm_invoke, "get_llm", lambda: fake_llm)
        return asyncio.run(llm_invoke.invoke_json(
            "提示词 {x}", {"x": "v"}, {"score": 0}, "unit_test_site",
            candidate_id=kw.get("candidate_id")))

    def test_success_records_tokens_and_cost(self, monkeypatch):
        monkeypatch.setattr(llm_invoke, "get_llm", lambda: _FakeLLM(
            _msg_json(usage_metadata={"input_tokens": 1000, "output_tokens": 200})))
        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 1.0)
        monkeypatch.setattr(budget.settings, "LLM_PRICE_CURRENCY", "CNY")
        monkeypatch.setattr(budget.settings, "LLM_OUTPUT_PRICE_PER_MILLION", 2.0)
        # 固定为不分时段：否则高峰时段跑测试时成本会翻倍，断言随时刻变化
        monkeypatch.setattr(budget.settings, "LLM_PEAK_MULTIPLIER", 1.0)

        out = asyncio.run(llm_invoke.invoke_json(
            "提示词 {x}", {"x": "v"}, {"score": 0}, "unit_test_site", candidate_id=42))
        assert out["score"] == 88

        log = _latest_log()
        assert log.call_site == "unit_test_site"
        assert log.candidate_id == 42
        assert log.input_tokens == 1000 and log.output_tokens == 200
        assert log.cost == pytest.approx(0.0014)  # 1000/1e6*1 + 200/1e6*2
        assert log.status == "ok"
        assert log.degraded is False
        assert log.usage_missing is False
        assert log.prompt_hash and len(log.prompt_hash) == 64

    def test_call_failure_degrades_and_marks(self, monkeypatch):
        monkeypatch.setattr(llm_invoke, "get_llm",
                            lambda: _FakeLLM(raises=RuntimeError("boom")))
        default = {"score": 70}
        out = asyncio.run(llm_invoke.invoke_json("p {x}", {"x": 1}, default, "unit_test_site"))
        assert out is default, "失败必须返回 default（降级分）"
        log = _latest_log()
        assert log.status == "failed" and log.degraded is True

    def test_json_parse_failure_degrades(self, monkeypatch):
        monkeypatch.setattr(llm_invoke, "get_llm",
                            lambda: _FakeLLM(_msg_json(content="这不是 JSON")))
        default = {"score": 60}
        out = asyncio.run(llm_invoke.invoke_json("p {x}", {"x": 1}, default, "unit_test_site"))
        assert out is default
        log = _latest_log()
        assert log.status == "failed" and log.degraded is True
        assert "JSONParseError" in (log.error or "")

    def test_usage_missing_flagged(self, monkeypatch):
        monkeypatch.setattr(llm_invoke, "get_llm",
                            lambda: _FakeLLM(_msg_json(response_metadata={})))
        asyncio.run(llm_invoke.invoke_json("p {x}", {"x": 1}, {}, "unit_test_site"))
        log = _latest_log()
        assert log.usage_missing is True
        assert log.input_tokens is None

    def test_budget_exceeded_not_swallowed(self, monkeypatch):
        """预算耗尽必须向上抛，不能伪装成兜底分——否则调用方以为评估过了"""
        async def _raise():
            raise budget.BudgetExceeded("预算用尽")
        monkeypatch.setattr(budget, "check_budget", _raise)
        monkeypatch.setattr(llm_invoke, "get_llm",
                            lambda: _FakeLLM(_msg_json(usage_metadata={})))
        with pytest.raises(budget.BudgetExceeded):
            asyncio.run(llm_invoke.invoke_json("p {x}", {"x": 1}, {"score": 70}, "unit_test_site"))
        # 预算拦截不算"发生过的调用"，不应写 ok 日志
        log = _latest_log()
        assert log is None or log.status != "ok"

    def test_custom_client_used(self, monkeypatch):
        """知识库问答传入自己的客户端，不能被默认客户端悄悄替换"""
        own = _FakeLLM(_msg_json(content="答复", usage_metadata={"input_tokens": 5,
                                                                 "output_tokens": 5}))
        monkeypatch.setattr(llm_invoke, "get_llm", lambda: _FakeLLM())
        out = asyncio.run(llm_invoke.invoke_text("p {x}", {"x": 1}, "d", "unit_test_site",
                                                 client=own))
        assert out == "答复"
        assert _latest_log().model == "fake-model"


def _async_ret(value):
    async def _f():
        return value
    return _f


# ================================================================ 冷启动回读
class TestColdStartBudget:
    """回归：服务重启后进程内计数为 0，若首次检查不回读数据库，
    就会在"今天已花掉上限"的情况下完全放行——保护等同失效。
    """

    def test_cold_start_reads_database(self, monkeypatch):
        from datetime import datetime
        from src.models.database import SessionLocal, LLMCallLog

        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ENABLED", True)
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ACTION", "halt")
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_AMOUNT", 0.01)
        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 1.0)
        monkeypatch.setattr(budget.settings, "LLM_PRICE_CURRENCY", "CNY")
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_REFRESH_EVERY", 20)

        # 窗口内先有一笔超限成本（必须写 currency，成本聚合按币种过滤）
        with SessionLocal() as db:
            db.add(LLMCallLog(created_at=datetime.utcnow(), call_site="cold_start_seed",
                              model="m", input_tokens=1_000_000, output_tokens=0,
                              cost=0.5, currency="CNY", latency_ms=1, status="ok",
                              degraded=False, usage_missing=False, prompt_hash="x" * 64))
            db.commit()

        try:
            asyncio.run(budget.reset_cache_for_test())
            with pytest.raises(budget.BudgetExceeded):
                asyncio.run(budget.check_budget())
        finally:
            with SessionLocal() as db:
                db.query(LLMCallLog).filter(
                    LLMCallLog.call_site == "cold_start_seed").delete()
                db.commit()

    def test_no_db_read_when_within_refresh_window(self, monkeypatch):
        """未到刷新间隔且已回读过时，不应每次都查库（性能考量）"""
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ENABLED", True)
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_ACTION", "halt")
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_AMOUNT", 100.0)
        monkeypatch.setattr(budget.settings, "LLM_INPUT_PRICE_PER_MILLION", 1.0)
        monkeypatch.setattr(budget.settings, "LLM_PRICE_CURRENCY", "CNY")
        monkeypatch.setattr(budget.settings, "LLM_BUDGET_REFRESH_EVERY", 20)

        async def _scenario():
            await budget.reset_cache_for_test()
            first = await budget.spent_in_window()   # 冷启动 → 查库
            second = await budget.spent_in_window()  # 未到间隔 → 走缓存
            return first, second

        first, second = asyncio.run(_scenario())
        assert first == pytest.approx(second)


# ================================================================ 日志清理
class TestPurgeOldLogs:
    def test_purge_removes_expired_only(self):
        from datetime import datetime, timedelta
        from src.models.database import purge_old_llm_logs

        uid = uuid.uuid4().hex[:8]
        with SessionLocal() as db:
            db.add(LLMCallLog(call_site=f"old_{uid}", model="m", created_at=datetime.utcnow()))
            db.add(LLMCallLog(call_site=f"new_{uid}", model="m",
                              created_at=datetime.utcnow() - timedelta(days=200)))
            db.commit()

        removed = purge_old_llm_logs(days=90)
        assert removed == 1
        with SessionLocal() as db:
            assert db.query(LLMCallLog).filter(LLMCallLog.call_site == f"new_{uid}").count() == 0
            assert db.query(LLMCallLog).filter(LLMCallLog.call_site == f"old_{uid}").count() == 1
            db.query(LLMCallLog).filter(LLMCallLog.call_site.like(f"%_{uid}")).delete(
                synchronize_session=False)
            db.commit()

    def test_purge_zero_days_disabled(self):
        from src.models.database import purge_old_llm_logs
        assert purge_old_llm_logs(days=0) == 0


# ================================================================ JSON 日志字段
class TestJsonLogMoneyFields:
    """结构化日志只带出白名单字段：金额字段一旦改名而白名单没跟上，
    日志里就会**静默丢掉花费**，排查成本问题时看不到数。"""

    def _format(self, msg, extra):
        import json
        import logging
        from src.logging_setup import JsonFormatter
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)
        for k, v in extra.items():
            setattr(rec, k, v)
        return json.loads(JsonFormatter().format(rec))

    def test_cost_and_currency_survive(self):
        out = self._format("llm call", {"call_site": "s", "cost": 1.25,
                                        "currency": "CNY", "limit": 5.0})
        assert out["cost"] == 1.25
        assert out["currency"] == "CNY"
        assert out["limit"] == 5.0

    def test_budget_block_log_fields(self):
        out = self._format("预算超限", {"status": "budget_blocked", "cost": 9.0,
                                        "currency": "CNY", "limit": 5.0})
        assert out["status"] == "budget_blocked"
        assert out["cost"] == 9.0