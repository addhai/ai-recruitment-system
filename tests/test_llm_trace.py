"""LLM 调用链归集：thread_id 通过 contextvars 穿透到埋点。

回归的隐患是"以为穿透了其实没有"——contextvars 在下面三条路径上任何一个失效，
埋点就会静默留空，而这些调用链事后无法补记。所以每种路径都单独断言，
并配一个反向对照（不设置时必须为空，防止其实是读到默认值）。
"""
import asyncio

import pytest
from langgraph.graph import END, StateGraph
from typing import TypedDict

from src.services import llm_invoke, trace
from src.models.database import SessionLocal, LLMCallLog

_TID = "wf-probe-thread"


class _S(TypedDict):
    a: str


def _graph_with(node_fn):
    b = StateGraph(_S)
    b.add_node("n", node_fn)
    b.set_entry_point("n")
    b.add_edge("n", END)
    return b.compile()


class TestContextPropagation:
    def test_direct_node_execution(self):
        async def node(state):
            return {"a": str(trace.current_thread_id())}

        with trace.trace_thread(_TID):
            out = asyncio.run(_graph_with(node).ainvoke({"a": ""}))
        assert out["a"] == _TID

    def test_through_asyncio_to_thread(self):
        """埋点调用 LLM 走的就是 asyncio.to_thread，这条路径必须穿透"""
        async def node(state):
            return {"a": str(await asyncio.to_thread(trace.current_thread_id))}

        with trace.trace_thread(_TID):
            out = asyncio.run(_graph_with(node).ainvoke({"a": ""}))
        assert out["a"] == _TID

    def test_through_nested_create_task(self):
        """节点内部自行派生任务时也要继承"""
        async def node(state):
            async def inner():
                return trace.current_thread_id()
            return {"a": str(await asyncio.create_task(inner()))}

        with trace.trace_thread(_TID):
            out = asyncio.run(_graph_with(node).ainvoke({"a": ""}))
        assert out["a"] == _TID

    def test_none_outside_trace(self):
        """反向对照：没有工作流上下文时必须为空，不是碰巧等于默认值"""
        async def node(state):
            return {"a": str(trace.current_thread_id())}

        out = asyncio.run(_graph_with(node).ainvoke({"a": ""}))
        assert out["a"] == "None"


class TestTraceContextScope:
    def test_none_is_noop(self):
        assert trace.current_thread_id() is None
        with trace.trace_thread(None):
            assert trace.current_thread_id() is None
        assert trace.current_thread_id() is None

    def test_restored_after_exit(self):
        with trace.trace_thread("a"):
            assert trace.current_thread_id() == "a"
            with trace.trace_thread("b"):
                assert trace.current_thread_id() == "b"
            assert trace.current_thread_id() == "a", "内层退出要回到外层的值"
        assert trace.current_thread_id() is None

    def test_restored_on_exception(self):
        with pytest.raises(RuntimeError):
            with trace.trace_thread("a"):
                raise RuntimeError("boom")
        assert trace.current_thread_id() is None, "异常路径也要复位，否则会串到下一次运行"

    def test_concurrent_runs_do_not_cross_contaminate(self):
        """多个候选人并发跑时不能互相串号"""
        async def one(tid):
            with trace.trace_thread(tid):
                await asyncio.sleep(0.01)
                return trace.current_thread_id()

        async def main():
            # gather 必须在循环内调用：写在 asyncio.run 的参数里会在循环外求值
            return await asyncio.gather(*[one(f"wf-{i}") for i in range(5)])

        assert list(asyncio.run(main())) == [f"wf-{i}" for i in range(5)]


class TestThreadIdLandsInLog:
    @pytest.fixture(autouse=True)
    def _clean(self):
        yield
        with SessionLocal() as db:
            db.query(LLMCallLog).filter(
                LLMCallLog.call_site == "trace_probe").delete()
            db.commit()

    def _write(self, site="trace_probe"):
        llm_invoke._write_log(
            call_site=site, model="m", model_served="m", base_url="",
            input_tokens=1, output_tokens=1, cost=0.0, latency_ms=1,
            status="ok", degraded=False, prompt_text="p", candidate_id=None,
            error=None)
        with SessionLocal() as db:
            return (db.query(LLMCallLog)
                    .filter(LLMCallLog.call_site == site)
                    .order_by(LLMCallLog.id.desc()).first())

    def test_thread_id_recorded_when_in_trace(self):
        with trace.trace_thread(_TID):
            row = self._write()
        assert row.thread_id == _TID

    def test_null_when_not_in_trace(self):
        """非工作流链路（知识库问答 / JD 解析）本来就没有 thread_id，
        该列留空是正确语义，不要硬塞假值"""
        row = self._write()
        assert row.thread_id is None
