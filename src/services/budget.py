"""LLM 成本预算控制。

诉求：一个简历上传会触发 4~8 次 LLM 调用（每次最长 45s 超时）。
没有预算上限时，批量筛简历或一个写错的内部脚本就能把账单打上去。

策略（对应"超限转人工"）：
- 窗口内累计成本超过阈值 → 抛 BudgetExceeded，**不发起本次调用**
- 工作流捕获后转入「待人工评估」终态，不产出半成品招聘决策
- 已花费成本换来的评分照常保留——预算耗尽不是数据错误

未配置单价（单价为 0）时自动跳过检查：宁可不做保护，也不能因为忘配价格就把业务卡死。
"""
import asyncio
from datetime import datetime, timedelta

from src.config import settings
from src.logging_setup import get_logger

logger = get_logger(__name__)


class BudgetExceeded(RuntimeError):
    """成本预算耗尽。调用方应在发起请求前捕获并转人工。"""


# 进程内累加缓存：每次都 SUM 全表太重，按 N 次回读一次数据库
_spent_usd: float = 0.0
_calls_since_refresh: int = 0
# 冷启动标记：首次检查必须先回读数据库，否则重启后保护完全失效
_never_read_db: bool = True
_lock = asyncio.Lock()


def _window_start():
    """按配置返回统计窗口起点"""
    if settings.LLM_BUDGET_PERIOD == "monthly":
        now = datetime.utcnow()
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


def pricing_configured() -> bool:
    """单价是否已配置。未配置时成本恒为 0，预算检查无意义。"""
    return (settings.LLM_INPUT_PRICE_PER_MILLION or 0) > 0 or \
           (settings.LLM_OUTPUT_PRICE_PER_MILLION or 0) > 0


def compute_cost_usd(input_tokens, output_tokens):
    """按配置单价折算成本；未配单价返回 0"""
    if not pricing_configured():
        return 0.0
    cost = 0.0
    if input_tokens:
        cost += input_tokens / 1_000_000 * settings.LLM_INPUT_PRICE_PER_MILLION
    if output_tokens:
        cost += output_tokens / 1_000_000 * settings.LLM_OUTPUT_PRICE_PER_MILLION
    return round(cost, 6)


async def add_spend(cost_usd: float) -> None:
    """累计本次调用的成本（异步安全）"""
    global _spent_usd, _calls_since_refresh
    async with _lock:
        _spent_usd += cost_usd or 0.0
        _calls_since_refresh += 1


async def spent_in_window() -> float:
    """返回当前窗口内已花成本 = 进程内累计 + 数据库回读。

    进程内累计覆盖本次启动以来的调用，数据库覆盖历史，两者相加即为窗口总量。

    **冷启动必须回读**：服务重启后进程内计数为 0，若此时直接返回 0，
    就会在"今天已经花掉上限"的情况下完全放行——保护等同失效。
    因此首次调用（或距上次回读超过 N 次）都强制查库。
    """
    global _spent_usd, _calls_since_refresh, _never_read_db

    every = max(1, settings.LLM_BUDGET_REFRESH_EVERY)
    need_refresh = _never_read_db or _calls_since_refresh >= every

    if need_refresh:
        from src.models.database import SessionLocal, LLMCallLog
        from sqlalchemy import func

        async with _lock:
            _calls_since_refresh = 0
            _never_read_db = False
            base = _spent_usd
        db = SessionLocal()
        try:
            persisted = (
                db.query(func.coalesce(func.sum(LLMCallLog.cost_usd), 0.0))
                .filter(LLMCallLog.created_at >= _window_start())
                .scalar()
            )
            return float(persisted or 0.0) + base
        except Exception as e:
            logger.warning("回读预算失败，按进程内累计估算", extra={"error": str(e)[:200]})
            return _spent_usd
        finally:
            db.close()

    return _spent_usd


async def check_budget() -> None:
    """发起 LLM 调用前预检；超限抛 BudgetExceeded。

    LLM_BUDGET_ACTION=warn 时只记录不拦截。
    """
    if not settings.LLM_BUDGET_ENABLED or settings.LLM_BUDGET_ACTION == "warn":
        return
    if not pricing_configured():
        # 单价未配置 → 所有调用成本记 0 → 阈值比较无意义，跳过保护
        return

    limit = settings.LLM_BUDGET_USD or 0
    if limit <= 0:
        return

    spent = await spent_in_window()
    if spent >= limit:
        msg = (f"LLM 成本预算已用尽：{settings.LLM_BUDGET_PERIOD} 已花 "
               f"${spent:.4f} / 上限 ${limit:.2f}")
        logger.warning("预算超限，停止后续 LLM 调用", extra={
            "status": "budget_blocked",
            "cost_usd": round(spent, 6),
        })
        raise BudgetExceeded(msg)


async def reset_cache_for_test() -> None:
    """测试用：清空进程内累加缓存并重置冷启动标记"""
    global _spent_usd, _calls_since_refresh, _never_read_db
    async with _lock:
        _spent_usd = 0.0
        _calls_since_refresh = 0
        _never_read_db = True