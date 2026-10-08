"""统一 LLM 接入层：调用 + 埋点 + 成本记账。

**为什么要有这一层**：项目原先有 4 处各自 `ChatOpenAI(...)` 构造客户端
（llm_json / job_parser / knowledge_base / mcp_tools），任何"加埋点"或
"换模型"都得改 4 遍，且极易漏。收敛到这里后，token 用量、耗时、成本、
降级标记只在一个地方产生。

**关键实现细节**：不能用 `chain = prompt | llm | parser` 的一行式写法——
解析器会把 `AIMessage` 吃掉，token 用量就取不到了。必须先拿到未解析的
消息，再交给解析器。

**降级 vs 预算**：两者在埋点里必须可区分。degraded=True 表示调用失败走了
规则兜底分（此前排查时误把兜底 70 分当成真实评分读过）；budget_blocked
表示预算耗尽根本没发起调用。
"""
import asyncio
import hashlib
import time
from datetime import datetime
from typing import Any, Dict, Optional

from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda

from src.config import settings
from src.logging_setup import get_logger
from src.services import budget

logger = get_logger(__name__)

# 复用 llm_json 里已有的客户端构造，不重复实例化
from src.services.llm_json import get_llm  # noqa: E402

_STATUS_OK = "ok"
_STATUS_FAILED = "failed"
_STATUS_BUDGET = "budget_blocked"


def _prompt_hash(prompt_text: str) -> str:
    return hashlib.sha256(prompt_text[:500].encode("utf-8")).hexdigest()


def _extract_usage(msg: Any):
    """三级兜底取 token 用量：usage_metadata → response_metadata → None。

    不同 langchain / 供应商版本字段位置不一致，取不到时置 usage_missing，
    成本记 0 但调用仍然计数——不能因为供应商没返回用量就丢失可观测性。
    """
    usage = getattr(msg, "usage_metadata", None)
    if isinstance(usage, dict) and (
            usage.get("input_tokens") is not None or usage.get("output_tokens") is not None):
        return usage.get("input_tokens"), usage.get("output_tokens")

    meta = getattr(msg, "response_metadata", None) or {}
    legacy = meta.get("token_usage") or meta.get("usage")
    if isinstance(legacy, dict):
        return (legacy.get("prompt_tokens") or legacy.get("input_tokens"),
                legacy.get("completion_tokens") or legacy.get("output_tokens"))

    return None, None


def _write_log(*, call_site: str, model: str, base_url: str,
               input_tokens, output_tokens, cost_usd: float,
               latency_ms: int, status: str, degraded: bool,
               prompt_text: str, candidate_id: Optional[int],
               error: Optional[str]) -> None:
    """写 LLMCallLog。任何写库失败都不能影响主流程。"""
    try:
        from src.models.database import SessionLocal, LLMCallLog, ensure_tables
        # 确保表存在：脚本/CLI 等不经应用启动的入口，先前会因缺表而静默丢埋点
        ensure_tables()

        db = SessionLocal()
        try:
            db.add(LLMCallLog(
                created_at=datetime.utcnow(),
                call_site=call_site,
                candidate_id=candidate_id,
                model=model,
                base_url=base_url,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                status=status,
                degraded=degraded,
                usage_missing=input_tokens is None and output_tokens is None,
                prompt_hash=_prompt_hash(prompt_text),
                error=(error or "")[:500] or None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # 埋点失败绝不能拖垮业务
        logger.warning("写 LLM 调用日志失败",
                       extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})


def _emit(call_site: str, model: str, in_tok, out_tok,
          cost_usd: float, latency_ms: int, status: str) -> None:
    logger.info("llm call", extra={
        "call_site": call_site,
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
        "status": status,
    })


def _client_info(llm=None):
    llm = llm or get_llm()
    return getattr(llm, "model_name", None) or getattr(llm, "model", "unknown"), \
           str(getattr(llm, "openai_api_base", "") or "")[:128]


async def _invoke_raw(prompt_text: str, variables: Dict[str, Any], call_site: str,
                      candidate_id: Optional[int], client=None):
    """预算预检 → 发起调用 → 返回 (AIMessage, 元信息)。

    client 为 None 时用默认客户端；知识库问答有自己的 temperature=0.3 客户端
    （与招聘评估的 0.1 不同，生成式回答需要更高随机性），通过此参数传入，
    这样既能统一埋点，又不会悄悄改掉它实际使用的模型。

    BudgetExceeded 由调用方决定如何处理（工作流要转人工，JD 解析要报错）。
    """
    await budget.check_budget()

    llm = client or get_llm()
    model, base_url = _client_info(llm)
    prompt = PromptTemplate(template=prompt_text, input_variables=list(variables.keys()))
    # 组链要求 Runnable；已是 Runnable 的原样使用（如测试桩的 RunnableLambda）
    runnable = llm if hasattr(llm, "invoke") and hasattr(llm, "input_schema") \
        else RunnableLambda(llm.invoke)
    chain = prompt | runnable

    started = time.time()
    msg = await asyncio.to_thread(chain.invoke, variables)
    latency_ms = int((time.time() - started) * 1000)
    in_tok, out_tok = _extract_usage(msg)
    cost = budget.compute_cost_usd(in_tok, out_tok)
    await budget.add_spend(cost)
    return msg, {
        "model": model, "base_url": base_url, "input_tokens": in_tok,
        "output_tokens": out_tok, "cost_usd": cost, "latency_ms": latency_ms,
    }


async def invoke_json(prompt_text: str, variables: Dict[str, Any],
                      default: Dict[str, Any], call_site: str, *,
                      candidate_id: Optional[int] = None, client=None) -> Dict[str, Any]:
    """调用 LLM 并返回 JSON 结果，同时完成埋点与成本记账。

    失败一律降级返回 default，并把 degraded 记进日志——
    评审时必须能区分"模型给了低分"和"模型挂了走兜底分"。
    """
    try:
        msg, meta = await _invoke_raw(prompt_text, variables, call_site,
                                      candidate_id, client)
    except budget.BudgetExceeded:
        # 预算耗尽：不写调用日志（没真正发生调用），由工作流转人工
        raise
    except Exception as e:
        from src.services.llm_json import extract_llm_error
        detail = extract_llm_error(e)
        logger.warning("LLM 调用失败，降级为规则结果",
                       extra={"call_site": call_site, "status": _STATUS_FAILED})
        _write_log(call_site=call_site, model="?", base_url="",
                   input_tokens=None, output_tokens=None, cost_usd=0.0,
                   latency_ms=0, status=_STATUS_FAILED, degraded=True,
                   prompt_text=prompt_text, candidate_id=candidate_id, error=detail)
        return default

    try:
        result = JsonOutputParser().invoke(msg)
    except Exception as e:
        detail = f"[JSONParseError] {str(e)[:200]}"
        _write_log(call_site=call_site, model=meta["model"], base_url=meta["base_url"],
                   input_tokens=meta["input_tokens"], output_tokens=meta["output_tokens"],
                   cost_usd=meta["cost_usd"], latency_ms=meta["latency_ms"],
                   status=_STATUS_FAILED, degraded=True, prompt_text=prompt_text,
                   candidate_id=candidate_id, error=detail)
        _emit(call_site, meta["model"], meta["input_tokens"], meta["output_tokens"],
              meta["cost_usd"], meta["latency_ms"], _STATUS_FAILED)
        return default

    _write_log(call_site=call_site, model=meta["model"], base_url=meta["base_url"],
               input_tokens=meta["input_tokens"], output_tokens=meta["output_tokens"],
               cost_usd=meta["cost_usd"], latency_ms=meta["latency_ms"],
               status=_STATUS_OK, degraded=False, prompt_text=prompt_text,
               candidate_id=candidate_id, error=None)
    _emit(call_site, meta["model"], meta["input_tokens"], meta["output_tokens"],
          meta["cost_usd"], meta["latency_ms"], _STATUS_OK)
    return result


async def invoke_text(prompt_text: str, variables: Dict[str, Any],
                      default: str, call_site: str, *,
                      candidate_id: Optional[int] = None, client=None,
                      raise_on_error: bool = False) -> str:
    """文本输出版（知识库 RAG 问答用），与 invoke_json 同源同样埋点。

    raise_on_error=True 时，调用失败不返回兜底串而是向上抛。
    知识库问答需要这个语义：它据此把 mode 标成 fallback，
    否则降级回答会被当成"模型正常作答"报出去，调用方无从分辨。
    埋点里 degraded 仍会照常记录，不受影响。
    """
    try:
        msg, meta = await _invoke_raw(prompt_text, variables, call_site,
                                      candidate_id, client)
    except budget.BudgetExceeded:
        raise
    except Exception as e:
        from src.services.llm_json import extract_llm_error
        detail = extract_llm_error(e)
        logger.warning("LLM 文本调用失败，使用兜底答案",
                       extra={"call_site": call_site, "status": _STATUS_FAILED})
        _write_log(call_site=call_site, model="?", base_url="",
                   input_tokens=None, output_tokens=None, cost_usd=0.0,
                   latency_ms=0, status=_STATUS_FAILED, degraded=True,
                   prompt_text=prompt_text, candidate_id=candidate_id, error=detail)
        if raise_on_error:
            raise
        return default

    result = StrOutputParser().invoke(msg)
    _write_log(call_site=call_site, model=meta["model"], base_url=meta["base_url"],
               input_tokens=meta["input_tokens"], output_tokens=meta["output_tokens"],
               cost_usd=meta["cost_usd"], latency_ms=meta["latency_ms"],
               status=_STATUS_OK, degraded=False, prompt_text=prompt_text,
               candidate_id=candidate_id, error=None)
    _emit(call_site, meta["model"], meta["input_tokens"], meta["output_tokens"],
          meta["cost_usd"], meta["latency_ms"], _STATUS_OK)
    return result