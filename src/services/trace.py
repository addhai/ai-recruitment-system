"""工作流运行的调用链上下文。

解决的问题：一次简历上传会触发 4~8 次 LLM 调用，埋点里只有 `candidate_id`，
同一候选人**重跑**时这些调用就混在一起分不开，排查"这次运行到底哪一步花了钱/
哪一步降级了"只能靠时间戳猜。

做法是把 `thread_id`（已存在于 `WorkflowRun.results`）挂到一个 ContextVar 上，
在驱动图的那段代码里设置，埋点写库时读取。

**为什么用 ContextVar 而不是给函数加参数**：埋点入口（invoke_json / invoke_text）
的调用点散布在 10 个图节点里，逐个加参数既侵入又容易漏；而 contextvars 已实测
能穿透 LangGraph 的节点执行、`asyncio.to_thread`（埋点正是这么调 LLM 的）
与节点内新建的 `asyncio.create_task`（见 tests/test_llm_trace.py）。
ContextVar 本身按任务隔离，多个候选人并发跑不会互相串号。
"""
import contextlib
import contextvars
from typing import Iterator, Optional

_CURRENT_THREAD: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "llm_trace_thread_id", default=None)


def current_thread_id() -> Optional[str]:
    """当前调用链所属的 thread_id；不在任何工作流运行中时为 None。

    非工作流的 LLM 调用（知识库问答、JD 解析）本来就没有 thread_id，
    返回 None 是正确语义——埋点该列留空，不要硬塞一个假值。
    """
    return _CURRENT_THREAD.get()


@contextlib.contextmanager
def trace_thread(thread_id: Optional[str]) -> Iterator[None]:
    """把这段执行期间发起的 LLM 调用都归到同一个 thread_id。

    用 `with` 包住整段驱动过程（含 astream 的迭代），而不是每个调用点单独设置：
    节点是在这段执行里派生的子任务，会继承当时的上下文。

    thread_id 为空时什么都不做——调用方不需要为"有没有上下文"写分支。
    """
    if not thread_id:
        yield
        return
    token = _CURRENT_THREAD.set(thread_id)
    try:
        yield
    finally:
        _CURRENT_THREAD.reset(token)
