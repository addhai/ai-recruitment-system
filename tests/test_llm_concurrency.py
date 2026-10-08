"""单元测试：LLM 并发闸门 + 实际服务模型版本捕获。"""
import asyncio

import pytest

from src.services import llm_invoke


class _SlowLLM:
    """记录同时在途调用数的假客户端。"""
    model_name = "fake-model"
    openai_api_base = "http://fake/v1"

    def __init__(self, delay=0.05):
        self.delay = delay
        self.in_flight = 0
        self.peak = 0

    def _run(self, _payload):
        from langchain_core.messages import AIMessage
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            import time
            time.sleep(self.delay)
            return AIMessage(content='{"ok": true}',
                             usage_metadata={"input_tokens": 1, "output_tokens": 1,
                                             "total_tokens": 2},
                             response_metadata={"model_name": "Vendor-Served-v9"})
        finally:
            self.in_flight -= 1

    def invoke(self, payload):
        return self._run(payload)

    @property
    def input_schema(self):
        return {}


class TestConcurrencyGate:
    def test_peak_concurrency_is_capped(self, monkeypatch):
        monkeypatch.setattr(llm_invoke.settings, "LLM_MAX_CONCURRENCY", 2)
        monkeypatch.setattr(llm_invoke, "_sem", None)
        monkeypatch.setattr(llm_invoke, "_sem_size", -1)
        fake = _SlowLLM()

        async def fan_out():
            return await asyncio.gather(*[
                llm_invoke.invoke_text("说句话 {n}", {"n": i}, "x",
                                       "concurrency_probe", client=fake)
                for i in range(6)
            ])

        asyncio.run(fan_out())
        assert fake.peak <= 2, f"同时在途峰值 {fake.peak} 超过上限 2"

    def test_zero_disables_gate(self, monkeypatch):
        monkeypatch.setattr(llm_invoke.settings, "LLM_MAX_CONCURRENCY", 0)
        monkeypatch.setattr(llm_invoke, "_sem", None)
        monkeypatch.setattr(llm_invoke, "_sem_size", -1)
        assert llm_invoke._semaphore() is None

    def test_semaphore_size_follows_config_change(self, monkeypatch):
        """运行时改配置要重建信号量，否则旧上限会一直生效"""
        monkeypatch.setattr(llm_invoke, "_sem", None)
        monkeypatch.setattr(llm_invoke, "_sem_size", -1)
        monkeypatch.setattr(llm_invoke.settings, "LLM_MAX_CONCURRENCY", 3)
        assert llm_invoke._semaphore() is not None
        assert llm_invoke._sem_size == 3
        monkeypatch.setattr(llm_invoke.settings, "LLM_MAX_CONCURRENCY", 7)
        llm_invoke._semaphore()
        assert llm_invoke._sem_size == 7


class TestServedModelCapture:
    def test_served_model_read_from_response_metadata(self):
        class _Msg:
            response_metadata = {"model_name": "DeepSeek-V4.1-Flash"}
        assert llm_invoke._extract_served_model(_Msg(), "deepseek-flash") \
            == "DeepSeek-V4.1-Flash"

    def test_falls_back_to_requested_name(self):
        class _Msg:
            response_metadata = {}
        assert llm_invoke._extract_served_model(_Msg(), "deepseek-flash") \
            == "deepseek-flash"

    def test_ignores_blank_and_non_string(self):
        class _Msg:
            response_metadata = {"model_name": "   ", "model": 123}
        assert llm_invoke._extract_served_model(_Msg(), "fallback") == "fallback"
