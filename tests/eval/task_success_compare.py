"""对比两份任务成功率报告，输出前后差异。

用法：
    python tests/eval/task_success_compare.py before.json after.json

口径与 task_success_eval.py 一致，见那里的 metric_definitions。
所有比率同时给分子/分母，避免出现孤立小数。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


# 指标名 → 结果字典里承载分子的键
_NUM_KEY = {
    "survival_rate": "survived",
    "clean_rate": "clean_conclusion",
    "useful_rate": "useful",
    "partial_credit_rate": "partial_credit",
}


def rate(a: dict, key: str) -> str:
    n = a.get("n", 0)
    num = a.get(_NUM_KEY.get(key, key), 0)
    return f"{num}/{n} = {a.get(key, 0):.3f}" if n else "-"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    before = load(sys.argv[1])
    after = load(sys.argv[2])

    print("=" * 78)
    print("任务成功率：改进前 → 改进后")
    print("=" * 78)
    print(f"注入实验 n/mode: {before['meta']['n_per_mode']} → {after['meta']['n_per_mode']}"
          f"   seed {before['meta']['seed']} → {after['meta']['seed']}")

    print("\n" + "-" * 78)
    print("按故障模式")
    print("-" * 78)
    print(f"{'模式':<20}{'存活率(前)':>18}{'存活率(后)':>18}{'变化':>10}")
    for mode in before["meta"]["modes"]:
        b = before["by_mode"].get(mode)
        a = after["by_mode"].get(mode)
        if not b or not a:
            continue
        d = a["survival_rate"] - b["survival_rate"]
        print(f"{mode:<20}"
              f"{rate(b,'survival_rate'):>18}"
              f"{rate(a,'survival_rate'):>18}"
              f"{d:>+10.3f}")

    print("\n" + "-" * 78)
    print("按注入节点")
    print("-" * 78)
    print(f"{'节点':<26}{'存活率(前)':>16}{'存活率(后)':>16}{'变化':>9}")
    for site in before["meta"]["eval_sites"]:
        b = before["by_site"].get(site)
        a = after["by_site"].get(site)
        if not b or not a:
            continue
        d = a["survival_rate"] - b["survival_rate"]
        print(f"{site:<26}"
              f"{rate(b,'survival_rate'):>16}"
              f"{rate(a,'survival_rate'):>16}"
              f"{d:>+9.3f}")

    print("\n" + "-" * 78)
    print("整体三项指标")
    print("-" * 78)
    bo, ao = before["overall"], after["overall"]
    labels = {
        "survival_rate": "存活率（任务没被异常打断）",
        "clean_rate": "干净率（全程无兜底分）",
        "useful_rate": "有结论率（落库了维度评分）",
        "partial_credit_rate": "部分有用率（失败但留有维度）",
    }
    print(f"{'指标':<30}{'前':>16}{'后':>16}{'变化':>9}")
    for k, label in labels.items():
        d = ao[k] - bo[k]
        print(f"{label:<30}{rate(bo,k):>16}{rate(ao,k):>16}{d:>+9.3f}")

    print("\n" + "-" * 78)
    print("终态分布")
    print("-" * 78)
    for tag, rep in (("前", before), ("后", after)):
        counts = rep["overall"]["run_status_counts"]
        s = "  ".join(f"{k}={v}" for k, v in counts.items())
        print(f"  {tag}: {s}")

    # 有效性守卫
    print("\n" + "-" * 78)
    print("有效性守卫")
    print("-" * 78)
    for tag, rep in (("前", before), ("后", after)):
        missed = rep["overall"].get("injection_missed", 0)
        ok = "OK（所有注入都命中）" if missed == 0 else f"!! 有 {missed} 个注入未命中"
        print(f"  {tag}: 注入未命中 {missed} —— {ok}")
    ctrl_b = before["by_mode"].get("control")
    ctrl_a = after["by_mode"].get("control")
    if ctrl_b and ctrl_a:
        print(f"  对照组存活率: 前 {ctrl_b['survival_rate']:.3f} / 后 {ctrl_a['survival_rate']:.3f}"
              f"（应为 1.000，低于 1 说明改动破坏了正常路径）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
