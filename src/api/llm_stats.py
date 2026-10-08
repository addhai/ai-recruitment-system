"""LLM 调用成本统计接口。

回答三个运营问题：钱花在哪（按 call_site）、健康度如何（失败率/降级率）、
用量多大（token 数）。成本明细不返回 prompt 原文，只给元数据。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, cast, Integer
from sqlalchemy.orm import Session

from src.config import settings
from src.models.database import get_db, LLMCallLog
from src.api.auth import require_hr_admin
from src.services import llm_config, budget

router = APIRouter(prefix="/llm-stats", tags=["llm-stats"])


@router.get("/summary")
def get_summary(
    days: int = Query(7, ge=1, le=365, description="统计最近多少天"),
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin),
):
    """按 call_site 聚合：调用次数、token、成本、失败率、降级率、耗时分布。"""
    since = datetime.utcnow() - timedelta(days=days)

    rows: List[Any] = (
        db.query(
            LLMCallLog.call_site,
            func.count(LLMCallLog.id),
            func.coalesce(func.sum(LLMCallLog.input_tokens), 0),
            func.coalesce(func.sum(LLMCallLog.output_tokens), 0),
            func.coalesce(func.sum(LLMCallLog.cost), 0.0),
            func.coalesce(func.avg(LLMCallLog.latency_ms), 0),
            func.coalesce(func.max(LLMCallLog.latency_ms), 0),
            func.sum(cast(LLMCallLog.status == "failed", Integer)),
            func.sum(cast(LLMCallLog.degraded == True, Integer)),  # noqa: E712
            func.sum(cast(LLMCallLog.usage_missing == True, Integer)),  # noqa: E712
        )
        .filter(LLMCallLog.created_at >= since)
        .group_by(LLMCallLog.call_site)
        .order_by(func.sum(LLMCallLog.cost).desc())
        .all()
    )

    by_site: List[Dict[str, Any]] = []
    total_calls = total_in = total_out = total_failed = total_degraded = 0
    total_cost = 0.0

    for (site, calls, in_tok, out_tok, cost, avg_ms, max_ms,
         failed, degraded, usage_missing) in rows:
        total_calls += calls or 0
        total_in += in_tok or 0
        total_out += out_tok or 0
        total_cost += cost or 0
        total_failed += failed or 0
        total_degraded += degraded or 0
        by_site.append({
            "call_site": site,
            "calls": calls or 0,
            "input_tokens": in_tok or 0,
            "output_tokens": out_tok or 0,
            "cost": round(cost or 0.0, 6),
            "avg_latency_ms": int(avg_ms or 0),
            "max_latency_ms": int(max_ms or 0),
            "failed": failed or 0,
            "degraded": degraded or 0,
            "usage_missing": usage_missing or 0,
            # 降级率高说明模型在挂或超时，评价值疑——必须与"低分"区分开看
            "degraded_rate": round((degraded or 0) / calls, 4) if calls else 0.0,
        })

    daily = (
        db.query(
            func.date(LLMCallLog.created_at).label("d"),
            func.count(LLMCallLog.id),
            func.coalesce(func.sum(LLMCallLog.cost), 0.0),
        )
        .filter(LLMCallLog.created_at >= since)
        .group_by("d")
        .order_by("d")
        .all()
    )

    effective = llm_config.get_effective_config()
    budget_state: Dict[str, Any] = {
        "enabled": settings.LLM_BUDGET_ENABLED,
        "action": settings.LLM_BUDGET_ACTION,
        "period": settings.LLM_BUDGET_PERIOD,
        "limit": budget.budget_limit(),
        "currency": effective["currency"],
        # 单价未配置时所有成本记 0，预算比较无意义，明确告知前端
        "pricing_configured": llm_config.is_pricing_configured(),
        "model": effective["model"],
        "config_source": effective["source"],
        "is_peak_now": llm_config.is_peak_now(),
    }

    return {
        "days": days,
        "currency": effective["currency"],
        "totals": {
            "calls": total_calls,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cost": round(total_cost, 6),
            "avg_cost_per_call": round(total_cost / total_calls, 6) if total_calls else 0.0,
            "failed": total_failed,
            "degraded": total_degraded,
            "failed_rate": round(total_failed / total_calls, 4) if total_calls else 0.0,
        },
        "by_site": by_site,
        "daily": [{"date": d, "calls": c, "cost": round(cost or 0.0, 6)}
                  for d, c, cost in daily],
        "budget": budget_state,
        "log_retention_days": settings.LLM_LOG_RETENTION_DAYS,
    }


@router.get("/calls")
def list_recent_calls(
    limit: int = Query(50, ge=1, le=200),
    call_site: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin),
):
    """最近的调用明细（元数据，不含 prompt 原文与模型输出）。"""
    query = db.query(LLMCallLog)
    if call_site:
        query = query.filter(LLMCallLog.call_site == call_site)
    rows = query.order_by(LLMCallLog.id.desc()).limit(limit).all()
    return [{
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "call_site": r.call_site,
        "candidate_id": r.candidate_id,
        "model": r.model,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cost": r.cost,
        "currency": r.currency,
        "latency_ms": r.latency_ms,
        "status": r.status,
        "degraded": r.degraded,
        "usage_missing": r.usage_missing,
        "prompt_hash": (r.prompt_hash or "")[:12],
        "error": r.error,
    } for r in rows]