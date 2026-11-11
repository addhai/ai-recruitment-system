# -*- coding: utf-8 -*-
"""任务质量前后对比：把两份跑批结果放在一起，逐项比。

## 为什么要有这个工具

三个目标（工具调用准确率 / 流程步骤数与成本 / 推理质量）如果各看各的，
很容易出现"调用数降了 20%，但分数也漂了 15 分"这种**用质量换成本**的情况——
而报告里只看成本那一栏，看不出问题。所以对比工具必须同时给出：

1. 成本/步骤数/延迟的变化；
2. 质量指标的变化；
3. **逐维度的分数漂移**（同一份简历，前后两次跑出来的分差多少）。

第 3 项是守门条件：漂移超过阈值时，即使成本降了也不能算改好。

## 用法

```powershell
python tests/eval/task_quality_compare.py before.json after.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 分数漂移守门阈值：同一份简历、同一个维度，前后两次跑批的分差。
# 超过它就说明改动影响了评分本身，必须人工看，不许只看成本那一栏。
SCORE_DRIFT_GATE = 10.0

# dim_scores 的键 与 evaluated_dims 里的中文标签 的对应关系。
#
# 为什么需要这张表：`dim_scores` 里缺省的维度会被填成 0，
# 而 0 有两种完全不同的含义——
#   (a) 真的评估了并给了 0 分；
#   (b) **根本没走到这一关**（例如早就在文化关卡被淘汰，问卷压根没生成）。
# 两轮跑批如果"到达的关卡不同"，naive 的差值会把 (b) 当成 (a)，
# 报出一个 ±50 分的"漂移"，而实际上一分都没漂——只是路径不同。
# 所以必须先按 `evaluated_dims` 判断这一维到底评没评过，两侧都评过才比。
DIM_LABELS = {
    "skill": "技能匹配",
    "experience": "经验匹配",
    "education": "教育匹配",
    "culture": "文化契合",
    "questionnaire": "问卷测评",
    "overall": "综合评审",
}


def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"找不到文件：{path}")
    return json.loads(p.read_text(encoding="utf-8"))


def _row(label: str, before, after, better: str = "") -> str:
    def fmt(v):
        if isinstance(v, float):
            return f"{v:.4f}"
        if v is None:
            return "-"
        return str(v)
    delta = ""
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        d = after - before
        delta = f"  ({d:+.4f})"
    return f"  {label:<22}{fmt(before):>14}{fmt(after):>14}{delta}  {better}"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    b = _load(sys.argv[1])
    a = _load(sys.argv[2])
    ob, oa = b.get("overall", {}), a.get("overall", {})

    print("=" * 78)
    print(f"任务质量前后对比   before n={ob.get('n')}   after n={oa.get('n')}")
    print(f"  before: {sys.argv[1]}")
    print(f"  after : {sys.argv[2]}")
    print("=" * 78)

    print("\n【一、工具调用准确率】")
    print(f"  {'指标':<22}{'before':>14}{'after':>14}")
    print(_row("受检调用数", ob.get("schema_checked_calls"), oa.get("schema_checked_calls")))
    print(_row("首次合规数", ob.get("schema_valid_first"), oa.get("schema_valid_first")))
    print(_row("首次结构合规率", ob.get("schema_first_valid_rate"),
               oa.get("schema_first_valid_rate"), "越高越好"))
    print(_row("发起修正次数", ob.get("schema_repaired_calls"), oa.get("schema_repaired_calls")))
    print(_row("修正后合规数", ob.get("schema_repaired_ok"), oa.get("schema_repaired_ok")))
    print(_row("修正成功率", ob.get("schema_repair_success_rate"),
               oa.get("schema_repair_success_rate"), "越高越好"))
    print(f"  不合规字段 before: {ob.get('schema_error_fields')}")
    print(f"  不合规字段 after : {oa.get('schema_error_fields')}")

    print("\n【二、流程步骤数与成本】")
    print(f"  {'指标':<22}{'before':>14}{'after':>14}")
    print(_row("单任务首次调用数", ob.get("avg_calls_first"), oa.get("avg_calls_first"), "越少越好"))
    print(_row("单任务总调用数", ob.get("avg_calls_total"), oa.get("avg_calls_total"), "越少越好"))
    print(_row("单任务成本(¥)", ob.get("avg_cost"), oa.get("avg_cost"), "越少越好"))
    print(_row("总花费(¥)", ob.get("total_cost"), oa.get("total_cost"), "越少越好"))
    print(_row("延迟 p50(ms)", ob.get("latency_p50_ms"), oa.get("latency_p50_ms"), "越低越好"))
    print(_row("延迟 p90(ms)", ob.get("latency_p90_ms"), oa.get("latency_p90_ms"), "越低越好"))
    print(_row("延迟 max(ms)", ob.get("latency_max_ms"), oa.get("latency_max_ms"), "越低越好"))

    print("\n【三、推理质量】")
    print(f"  {'指标':<22}{'before':>14}{'after':>14}")
    print(_row("决策精确一致率", ob.get("decision_exact_rate"),
               oa.get("decision_exact_rate"), "越高越好"))
    print(_row("决策档位对齐率", ob.get("decision_aligned_rate"),
               oa.get("decision_aligned_rate"), "越高越好"))
    print(_row("理由-分数自洽率", ob.get("reason_consistent_rate"),
               oa.get("reason_consistent_rate"), "越高越好"))
    print(_row("裁判决策一致率", ob.get("judge_decision_consistent_rate"),
               oa.get("judge_decision_consistent_rate"), "越高越好"))
    print(_row("裁判理由自洽率", ob.get("judge_reason_consistent_rate"),
               oa.get("judge_reason_consistent_rate"), "越高越好"))
    print(_row("裁判幻觉率", ob.get("judge_hallucination_rate"),
               oa.get("judge_hallucination_rate"), "越低越好"))
    print(_row("裁判质量均分", ob.get("judge_quality_avg"),
               oa.get("judge_quality_avg"), "越高越好"))
    print(_row("裁判质量最低分", ob.get("judge_quality_min"),
               oa.get("judge_quality_min"), "越高越好"))

    print("\n【四、分数漂移守门】")
    print(f"  同一份简历、同一维度，前后两次跑批的分差；阈值 {SCORE_DRIFT_GATE} 分。")
    print("  只比**两侧都真正评估过**的维度：某一侧没走到这一关时分记 0，")
    print("  那是路径不同，不是分数漂移。")
    bmap = {r.get("profile"): r for r in b.get("records", [])}
    amap = {r.get("profile"): r for r in a.get("records", [])}
    worst = 0.0
    drifts = []
    skipped_path = []
    for profile in sorted(set(bmap) & set(amap)):
        rb, ra = bmap[profile], amap[profile]
        sb, sa = rb.get("dim_scores") or {}, ra.get("dim_scores") or {}
        eb = set(rb.get("evaluated_dims") or [])
        ea = set(ra.get("evaluated_dims") or [])
        for dim, label in DIM_LABELS.items():
            vb, va = sb.get(dim), sa.get(dim)
            if not isinstance(vb, (int, float)) or not isinstance(va, (int, float)):
                continue
            if label not in eb or label not in ea:
                skipped_path.append((profile, dim, label in eb, label in ea, vb, va))
                continue
            d = float(va) - float(vb)
            worst = max(worst, abs(d))
            drifts.append((profile, dim, vb, va, d))
    if not drifts:
        print("  （两次跑批没有可比的 profile）")
    else:
        over = [d for d in drifts if abs(d[4]) > SCORE_DRIFT_GATE]
        print(f"  可比维度数 {len(drifts)}  最大漂移 {worst:.1f} 分  "
              f"超阈值 {len(over)} 处")
        for profile, dim, vb, va, d in sorted(drifts, key=lambda x: -abs(x[4]))[:10]:
            flag = "  <-- 超阈值" if abs(d) > SCORE_DRIFT_GATE else ""
            print(f"    {profile:<18}{dim:<14}{vb:>6} -> {va:>6}  ({d:+.1f}){flag}")
        if not over:
            print("  结论：没有维度漂移超过阈值。")
    if skipped_path:
        print(f"\n  因「一侧没走到该关卡」而未参与比对的维度 {len(skipped_path)} 处：")
        for profile, dim, inb, ina, vb, va in skipped_path[:10]:
            print(f"    {profile:<18}{dim:<14}before {'评过' if inb else '没走到'}"
                  f"  after {'评过' if ina else '没走到'}  ({vb} / {va})")

    print("\n【五、样本与口径】")
    for label, o in (("before", ob), ("after", oa)):
        print(f"  {label}: n={o.get('n')}  受检调用={o.get('schema_checked_calls')}  "
              f"修正={o.get('schema_repaired_calls')}  "
              f"裁判样本={o.get('judge_n', '-')}  "
              f"决策可比样本={o.get('decision_exact_n', '-')}")
    print("  所有比率的分子/分母见上；样本量小的地方不要当成结论。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
