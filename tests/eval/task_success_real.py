"""真实任务成功率评测：不 mock LLM，跑真实的端到端招聘工作流。

## 为什么注入实验不够

注入实验回答的是"注入已知故障时任务能否存活"，但它不代表**正常路径**的表现：
- 正常路径的失败来自随机因素（超时、限流、上游抖动），注入实验测不到；
- 正常路径才是生产上 99% 的流量，它的耗时与成本才是真实成本。

所以这里用真实 LLM 跑 N 个完整任务，量三件事：
1. 任务终态分布（completed / waiting_human / partial / failed）
2. 结论有效性（有没有落库维度评分、有没有维度用了兜底分）
3. **耗时与花费**（重试次数与超时的改动直接影响这两项）

## 隔离

用独立的临时 SQLite，不碰用户的 recruitment.db。
副作用要说清楚：预算墙读的是库里的 llm_call_logs，隔离库里是空的，
所以**本实验不受预算墙拦截**——这是有意的（否则任务会被中途掐断，
测出来的就不是"正常路径成功率"）。花费单独统计并如实报告。

用法：
    python tests/eval/task_success_real.py --n 15 --out task_success_real_after.json
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_TMP = tempfile.mkdtemp(prefix="task_success_real_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_TMP) / "real.db")
os.environ.setdefault("SECRET_KEY", "eval-secret")
# 刻意**不**设置 LLM_API_KEY：os.environ 的优先级高于 .env，
# 一旦在这里塞个占位 key，就会把 .env 里的真 key 盖掉，
# 结果是每次调用都 401 失败、全部降级——而且日志只写"调用失败"，
# 看不出是 key 的问题。（注入实验用 mock，不受此影响，可以随便设。）
os.environ["LLM_BUDGET_ENABLED"] = "false"
os.environ["RERANK_ENABLED"] = "false"
os.environ["LLM_MAX_CONCURRENCY"] = "0"
sys.path.insert(0, str(REPO_ROOT))

# 复用注入实验里的固定装置与健康载荷，保证两套实验用同一套 fixture
_spec = importlib.util.spec_from_file_location(
    "tse", REPO_ROOT / "tests" / "eval" / "task_success_eval.py")
tse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tse)

# 兜底：万一注入实验模块又设了占位 key，这里清掉。
# 注意 src.config.settings 是 import 时创建的模块级单例，事后改 os.environ
# 对它无效——所以真正的修复是"注入实验根本别设这个 key"。
if os.environ.get("LLM_API_KEY") == "eval-key":
    os.environ.pop("LLM_API_KEY", None)

from src.models.database import Evaluation, SessionLocal, WorkflowRun, init_db  # noqa: E402
from src.workflow import runner  # noqa: E402


async def _run_one(index: int) -> dict:
    cid = tse._new_candidate(tse._ensure_jd())
    t0 = time.time()
    try:
        out = await asyncio.wait_for(
            runner.start_workflow(cid, position_requirements="后端工程师"),
            timeout=300.0,
        )
        status = out.get("status")
        error = out.get("error")
    except asyncio.TimeoutError:
        status, error = "timeout", "任务超过 300s 未结束"
    except Exception as e:
        status, error = "exception", f"{type(e).__name__}: {e}"
    elapsed_ms = int((time.time() - t0) * 1000)

    with SessionLocal() as db:
        run = (db.query(WorkflowRun)
               .filter(WorkflowRun.candidate_id == cid)
               .order_by(WorkflowRun.id.desc()).first())
        run_status = run.status if run else None
        results = run.results if run and run.results else {}
        evals = db.query(Evaluation).filter(Evaluation.candidate_id == cid).all()
        dims = sorted({e.dimension for e in evals})
        cost = sum(e for e in (results.get("cost_spent"),) if isinstance(e, (int, float)))
        # 真实花费从本次运行的埋点里读（终态可能不写 cost_spent）
        from src.models.database import LLMCallLog
        rows = db.query(LLMCallLog).filter(
            LLMCallLog.thread_id == results.get("thread_id")).all() if results.get("thread_id") else []
        call_cost = sum((r.cost or 0.0) for r in rows)
        call_errors = [{"site": r.call_site, "status": r.status,
                        "error": str(r.error or "")[:200]} for r in rows
                       if (r.status or "ok") != "ok"]

    return {
        "index": index,
        "status": status,
        "run_status": run_status,
        "error": (error or "")[:300] if error else None,
        "elapsed_ms": elapsed_ms,
        "evaluated_dims": dims,
        "evaluated_count": len(dims),
        "degraded_dims_reported": list(results.get("degraded_dimensions") or []),
        "assessed_dimensions": list(results.get("assessed_dimensions") or []),
        "thread_id": results.get("thread_id"),
        "cost": round(call_cost, 5),
        "calls": len(rows),
        "call_errors": call_errors,
        "failed_step": results.get("failed_step"),
    }


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--out", default="task_success_real.json")
    args = ap.parse_args()

    init_db()
    runner._CHECKPOINT_DB = str(Path(_TMP) / "cp.db")

    records: list[dict] = []

    async def run_all():
        try:
            for i in range(args.n):
                r = await _run_one(i)
                records.append(r)
                print(f"  [{i + 1}/{args.n}] run_status={str(r['run_status']):<14} "
                      f"耗时={r['elapsed_ms'] / 1000:6.1f}s  维度={r['evaluated_count']}  "
                      f"降级={len(r['degraded_dims_reported'])}  ¥{r['cost']:.4f}", flush=True)
                for e in r.get("call_errors", [])[:3]:
                    print(f"        ! {e['site']}: {e['status']} {e['error']}", flush=True)
        finally:
            await runner.close_graph()

    asyncio.run(run_all())

    n = len(records)
    survived = sum(1 for r in records
                   if r["run_status"] in ("waiting_human", "completed", "budget_halted", "partial"))
    useful = sum(1 for r in records if r["evaluated_count"] > 0)
    clean = sum(1 for r in records if r["evaluated_count"] > 0
                and not r["degraded_dims_reported"])
    lat = [r["elapsed_ms"] for r in records]
    cost = sum(r["cost"] for r in records)

    status_counts: dict[str, int] = {}
    for r in records:
        status_counts[str(r["run_status"])] = status_counts.get(str(r["run_status"]), 0) + 1

    report = {
        "meta": {"n": n, "mode": "real_llm", "note": "隔离临时库，不受预算墙拦截；花费单独统计"},
        "overall": {
            "n": n,
            "survived": survived,
            "survival_rate": round(survived / n, 4) if n else 0,
            "useful": useful,
            "useful_rate": round(useful / n, 4) if n else 0,
            "clean": clean,
            "clean_rate": round(clean / n, 4) if n else 0,
            "run_status_counts": status_counts,
            "latency_p50_ms": int(_pct(lat, .5)),
            "latency_p90_ms": int(_pct(lat, .9)),
            "latency_max_ms": int(max(lat)) if lat else 0,
            "total_cost": round(cost, 4),
            "avg_cost": round(cost / n, 5) if n else 0,
            "avg_calls": round(sum(r["calls"] for r in records) / n, 2) if n else 0,
        },
        "records": records,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    o = report["overall"]
    print("\n" + "=" * 68)
    print(f"真实任务汇总  n={n}")
    print("=" * 68)
    print(f"  存活率     {o['survived']}/{n} = {o['survival_rate']:.3f}")
    print(f"  有结论率   {o['useful']}/{n} = {o['useful_rate']:.3f}")
    print(f"  干净率     {o['clean']}/{n} = {o['clean_rate']:.3f}")
    print(f"  终态       {o['run_status_counts']}")
    print(f"  延迟       p50={o['latency_p50_ms']}ms  p90={o['latency_p90_ms']}ms  max={o['latency_max_ms']}ms")
    print(f"  花费       总 ¥{o['total_cost']:.4f}  单任务均 ¥{o['avg_cost']:.5f}  "
          f"平均 {o['avg_calls']} 次调用")
    print(f"  报告       {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
