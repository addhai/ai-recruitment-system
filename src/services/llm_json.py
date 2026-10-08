"""LLM JSON 调用统一入口（兼容包装层）。

实现已下沉到 `src/services/llm_invoke.py`（含埋点与成本记账）。
这里保留 `llm_json` 这个名字与原有签名，因为：

1. `recruitment_graph.py` 里以 `_llm_json` 之名导入，测试中有 40 处
   `monkeypatch.setattr(rg, "_llm_json", ...)` 依赖这个绑定点；
2. 保持函数签名不变，11 个工作流节点无需改动调用形态。

新增能力通过可选参数 call_site 传入——它决定埋点里这次调用被记到哪一步。
"""
import json
from typing import Any, Dict, Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from src.config import settings
from src.logging_setup import get_logger

logger = get_logger(__name__)

_llm_instance: Optional[ChatOpenAI] = None


def get_llm() -> ChatOpenAI:
    """延迟初始化 LLM，避免启动时无 API Key 报错。

    评分类任务要求低温度（0.1）保证同分输入结果稳定可复现，
    温度偏高会让同一候选人两次跑分差拉大，破坏鉴别力验证。
    """
    global _llm_instance
    if _llm_instance is None:
        if not settings.LLM_API_KEY:
            raise RuntimeError("LLM API Key 未配置")
        _llm_instance = ChatOpenAI(
            model=settings.LLM_MODEL,
            temperature=0.1,
            timeout=45,
            max_retries=2,
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )
    return _llm_instance


def extract_llm_error(e: Exception) -> str:
    """从 langchain/openai 异常中提取 HTTP 状态码与响应体 message。

    避免把 401(key 错)、404(模型名错)、429(限流)、超时 全部吞成同一句"调用失败"。
    """
    status = getattr(getattr(e, "response", None), "status_code", None)
    body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = json.loads(resp.text).get("error", {}).get("message", "")[:200]
        except Exception:
            body = str(getattr(resp, "text", ""))[:200]
    etype = type(e).__name__
    return f"[{etype}] HTTP {status or '?'} {body or str(e)[:200]}"


async def llm_json(prompt_text: str, variables: Dict[str, Any],
                   default: Dict[str, Any], call_site: str = "unknown",
                   *, candidate_id: Optional[int] = None) -> Dict[str, Any]:
    """转调统一接入层完成调用与埋点。

    注意：预算耗尽时 BudgetExceeded 会向上抛，由工作流转人工处理，
    这里**不吞**——否则调用方以为拿到了兜底分，实际根本没评估。
    """
    from src.services.llm_invoke import invoke_json
    return await invoke_json(prompt_text, variables, default, call_site,
                             candidate_id=candidate_id)