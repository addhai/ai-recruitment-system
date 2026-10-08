"""模型接入配置：由使用者在界面上切换模型，而不是把某家供应商写死在代码里。

设计要点：
1. **本项目不是模型中转站**，不预置固定接入。模型名、API Key、Base URL、
   单价都由使用者填写；.env 仅作兜底默认值。
2. **API Key 加密存储**（用 SECRET_KEY 派生密钥），接口永不回传明文，
   只回传掩码预览。密钥若随接口响应或日志泄露，等于把账号交出去。
3. **分时计价**：DeepSeek 官方按高峰/空闲两档计费（空闲为高峰的一半），
   高峰=北京时间周一至周五 9:00-12:00、14:00-18:00。
   法定节假日未纳入计算（会略微高估成本），已在配置项注释中说明。
4. 配置变更后清空客户端缓存，无需重启即可生效。
"""
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from src.config import settings
from src.logging_setup import get_logger

logger = get_logger(__name__)

# 北京时间（UTC+8）
_CN_TZ = timezone(timedelta(hours=8))
_PEAK_WINDOWS = ((9, 12), (14, 18))

# DeepSeek 官方定价预设（人民币/百万 token，来源：官方「模型 & 价格」页）。
# 空闲时段单价；高峰 = 空闲 × 2。
DEEPSEEK_PRESETS: Dict[str, Dict[str, Any]] = {
    "deepseek-flash": {
        "label": "deepseek-flash（DeepSeek-V4.1-Flash，1M 上下文，支持图像理解）",
        "base_url": "https://api.deepseek.com",
        "input_price": 1.0,       # 缓存未命中·空闲
        "output_price": 4.0,      # 空闲
        "currency": "CNY",
        "peak_multiplier": 2.0,
        "temperature": 0.1,
        "timeout_seconds": 45,
        "note": "并发限制 2500。旧模型名 deepseek-v4-flash 仍可调用但已下线，按 Flash 价格计费。",
    },
    "deepseek-v4-pro": {
        "label": "deepseek-v4-pro（DeepSeek-V4-Pro-0813，不支持图像理解）",
        "base_url": "https://api.deepseek.com",
        "input_price": 4.5,       # 缓存未命中·空闲
        "output_price": 13.5,     # 空闲
        "currency": "CNY",
        "peak_multiplier": 2.0,
        "temperature": 0.1,
        "timeout_seconds": 45,
        "note": "并发限制 500。",
    },
}


# ---------------------------------------------------------------- 密钥加解密

def _fernet():
    """用 SECRET_KEY 派生 Fernet 密钥。SECRET_KEY 变更会导致旧密文无法解密。"""
    from cryptography.fernet import Fernet
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(raw: str) -> str:
    if not raw:
        return ""
    return _fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def decrypt_api_key(token: str) -> Optional[str]:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as e:
        # SECRET_KEY 轮换后旧密文不可解 —— 明确告警并回退 .env，不静默失败
        logger.warning("API Key 解密失败（SECRET_KEY 可能已更换），回退 .env 配置: %s",
                       type(e).__name__)
        return None


def mask_api_key(raw: Optional[str]) -> str:
    """掩码预览：只露头尾，供界面确认'填的是哪把钥匙'"""
    if not raw:
        return ""
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}{'*' * 6}{raw[-4:]}"


# ---------------------------------------------------------------- 读取配置

def _row():
    from src.models.database import SessionLocal, LLMSettings, ensure_tables
    ensure_tables()
    db = SessionLocal()
    try:
        return db.query(LLMSettings).filter(LLMSettings.id == 1).first()
    finally:
        db.close()


def get_effective_config() -> Dict[str, Any]:
    """返回当前生效的模型配置：数据库优先，缺失项回退 .env。"""
    row = _row()
    db_key = decrypt_api_key(row.api_key_encrypted) if row else None

    return {
        "model": (row.model if row and row.model else None) or settings.LLM_MODEL,
        "base_url": (row.base_url if row and row.base_url else None) or settings.LLM_API_BASE,
        "api_key": db_key or settings.LLM_API_KEY,
        "input_price": (row.input_price if row and row.input_price is not None
                        else settings.LLM_INPUT_PRICE_PER_MILLION),
        "output_price": (row.output_price if row and row.output_price is not None
                         else settings.LLM_OUTPUT_PRICE_PER_MILLION),
        "currency": (row.currency if row and row.currency else None) or settings.LLM_PRICE_CURRENCY,
        "peak_multiplier": (row.peak_multiplier if row and row.peak_multiplier is not None
                            else settings.LLM_PEAK_MULTIPLIER),
        "temperature": (row.temperature if row and row.temperature is not None
                        else settings.LLM_TEMPERATURE),
        "timeout_seconds": (row.timeout_seconds if row and row.timeout_seconds
                            else settings.LLM_TIMEOUT_SECONDS),
        "source": "database" if (row and (row.model or row.api_key_encrypted)) else "env",
    }


def is_pricing_configured() -> bool:
    cfg = get_effective_config()
    return (cfg["input_price"] or 0) > 0 or (cfg["output_price"] or 0) > 0


# ---------------------------------------------------------------- 分时计价

def is_peak_now(now: Optional[datetime] = None) -> bool:
    """是否处于高峰计价时段。

    高峰 = 北京时间周一至周五（不含法定节假日）9:00-12:00 与 14:00-18:00。
    **法定节假日未纳入**：那些时段会被算成高峰价，导致成本略微高估
    （宁可高估触发预算保护，也不要低估让账单超出预期）。
    """
    cn_now = (now or datetime.now(timezone.utc)).astimezone(_CN_TZ)
    if cn_now.weekday() >= 5:      # 周六周日全天空闲
        return False
    hour = cn_now.hour
    return any(start <= hour < end for start, end in _PEAK_WINDOWS)


def compute_cost(input_tokens: Optional[int], output_tokens: Optional[int],
                 now: Optional[datetime] = None) -> float:
    """按当前生效配置折算成本（含分时计价）。"""
    cfg = get_effective_config()
    if not is_pricing_configured():
        return 0.0
    multiplier = 1.0
    peak = cfg["peak_multiplier"] or 1.0
    if peak > 1.0 and is_peak_now(now):
        multiplier = peak

    cost = 0.0
    if input_tokens:
        cost += input_tokens / 1_000_000 * (cfg["input_price"] or 0) * multiplier
    if output_tokens:
        cost += output_tokens / 1_000_000 * (cfg["output_price"] or 0) * multiplier
    return round(cost, 6)


# ---------------------------------------------------------------- 写入配置

def save_config(*, model: Optional[str] = None, base_url: Optional[str] = None,
                api_key: Optional[str] = None, input_price: Optional[float] = None,
                output_price: Optional[float] = None, currency: Optional[str] = None,
                peak_multiplier: Optional[float] = None,
                temperature: Optional[float] = None,
                timeout_seconds: Optional[int] = None,
                updated_by: Optional[int] = None) -> Dict[str, Any]:
    """保存模型配置并清空客户端缓存（无需重启即生效）。

    api_key 传 None 表示"不修改"，传空字符串表示"清空改用 .env"。
    """
    from src.models.database import SessionLocal, LLMSettings, ensure_tables
    ensure_tables()
    db = SessionLocal()
    try:
        row = db.query(LLMSettings).filter(LLMSettings.id == 1).first()
        if row is None:
            row = LLMSettings(id=1)
            db.add(row)

        if model is not None:
            row.model = model or None
        if base_url is not None:
            row.base_url = base_url or None
        if api_key is not None:
            row.api_key_encrypted = encrypt_api_key(api_key.strip()) if api_key.strip() else None
        if input_price is not None:
            row.input_price = input_price
        if output_price is not None:
            row.output_price = output_price
        if currency is not None:
            row.currency = currency or "CNY"
        if peak_multiplier is not None:
            row.peak_multiplier = peak_multiplier
        if temperature is not None:
            row.temperature = temperature
        if timeout_seconds is not None:
            row.timeout_seconds = timeout_seconds
        row.updated_by = updated_by
        db.commit()
    finally:
        db.close()

    invalidate_client_cache()
    logger.info("模型配置已更新", extra={"model": model or "(未变)"})
    return get_effective_config()


def invalidate_client_cache() -> None:
    """清空 LLM 客户端缓存，使新配置立即生效。"""
    try:
        from src.services import llm_json
        llm_json._llm_cache.clear()
    except Exception:
        pass
    # 知识库有独立的问答链（temperature 0.3），需一并重建
    try:
        from src.rag import knowledge_base as kb
        kb._init_attempted = False
        kb._chat_llm = None
    except Exception:
        pass


def masked_view() -> Dict[str, Any]:
    """给接口用的视图：密钥只回掩码，不回明文。"""
    cfg = get_effective_config()
    return {
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "api_key_masked": mask_api_key(cfg["api_key"]),
        "api_key_configured": bool(cfg["api_key"]),
        "input_price": cfg["input_price"],
        "output_price": cfg["output_price"],
        "currency": cfg["currency"],
        "peak_multiplier": cfg["peak_multiplier"],
        "temperature": cfg["temperature"],
        "timeout_seconds": cfg["timeout_seconds"],
        "source": cfg["source"],
        "pricing_configured": is_pricing_configured(),
        "is_peak_now": is_peak_now(),
    }


def list_presets() -> Dict[str, Any]:
    return {"presets": DEEPSEEK_PRESETS,
            "note": "预设以 DeepSeek 官方「模型 & 价格」页为准，价格可能变动，请按需核对。"}


async def test_connection() -> Dict[str, Any]:
    """用当前配置发一次最小调用，验证模型名/密钥/Base URL 是否可用。

    这是配置界面的关键一环：填错了要当场知道，而不是等到跑候选人评估时才失败。
    """
    import asyncio
    from langchain_core.messages import HumanMessage

    cfg = get_effective_config()
    if not cfg["api_key"]:
        return {"ok": False, "error": "未配置 API Key"}

    def _invoke():
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=cfg["model"], api_key=cfg["api_key"],
                         base_url=cfg["base_url"], temperature=0,
                         timeout=min(cfg["timeout_seconds"] or 30, 30), max_retries=0)
        return llm.invoke([HumanMessage(content="ping")])

    try:
        msg = await asyncio.to_thread(_invoke)
        usage = getattr(msg, "usage_metadata", None) or {}
        return {"ok": True, "model": cfg["model"],
                "reply_preview": str(getattr(msg, "content", ""))[:40],
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens")}
    except Exception as e:
        from src.services.llm_json import extract_llm_error
        return {"ok": False, "error": extract_llm_error(e), "model": cfg["model"]}
