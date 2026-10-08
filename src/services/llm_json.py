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

# 客户端按 (model, base_url, api_key) 缓存。用 dict 而非单一实例，
# 这样界面上切换模型后能立即生效，不必重启服务。
_llm_cache: Dict[tuple, ChatOpenAI] = {}


def get_llm(temperature: Optional[float] = None) -> ChatOpenAI:
    """按当前生效配置构造客户端（数据库配置优先，回退 .env）。

    评分类任务要求低温度（0.1）保证同分输入结果稳定可复现，
    温度偏高会让同一候选人两次跑分差拉大，破坏鉴别力验证。
    """
    from src.services import llm_config

    cfg = llm_config.get_effective_config()
    if not cfg["api_key"]:
        raise RuntimeError("LLM API Key 未配置（可在「系统设置 → 模型配置」中填写）")

    temp = temperature if temperature is not None else (cfg["temperature"] or 0.1)
    key = (cfg["model"], cfg["base_url"], cfg["api_key"], temp)
    llm = _llm_cache.get(key)
    if llm is None:
        llm = ChatOpenAI(
            model=cfg["model"],
            temperature=temp,
            timeout=cfg["timeout_seconds"] or settings.LLM_TIMEOUT_SECONDS,
            max_retries=2,
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
        )
        _llm_cache[key] = llm
    return llm


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