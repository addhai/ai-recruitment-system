# -*- coding: utf-8 -*-
"""chunk 策略回归门禁：把知识库检索黄金集接进日常 pytest。

## 为什么它能进日常 CI

走 BM25 路径，全部计算在本进程内：**无网络、无 API 成本、结果可复现**。
招聘评分层做不到这点——它要真调 LLM，有固定成本且采样不可复现，
所以那一层留在 `tests/eval/run_eval.py` 手动触发（见 tests/eval/README.md）。

## 它拦什么

1. **切分不变量被破坏**：文档整篇丢失、块超长、内容被切掉或乱序、元数据缺失。
2. **检索命中退化**：改 chunk 策略或换 embedding 模型后，原来能查到的查不到了。
3. **策略边界被悄悄突破**：见下面 `test_chunk_size_400_is_still_safe`。
"""
import json

import pytest

from tests.eval.run_chunk_eval import CASES_FILE, run_suite


@pytest.fixture(scope="module")
def golden_cases():
    """检索黄金集。内容缺失就直接报错——门禁不能静默跳过。"""
    assert CASES_FILE.exists(), f"黄金集不存在: {CASES_FILE}"
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    assert cases, "黄金集为空，检索回归无从谈起"
    return cases


def _failures_text(report):
    lines = []
    for c in report["invariants"]:
        if not c["ok"]:
            lines.append(f"  [不变量] {c['kind']}: {c['detail']}")
    for r in report["results"]:
        if not r["passed"]:
            lines.append(f"  [用例] {r['name']} (query={r['query']})")
            for f in r["failures"]:
                lines.append(f"      ✗ {f}")
    return "\n".join(lines) or "  （无失败详情）"


def test_split_invariants_hold(golden_cases):
    """切分不变量：与具体策略无关，任何参数下都必须成立。"""
    report = run_suite()
    broken = [c for c in report["invariants"] if not c["ok"]]
    assert not broken, (
        f"切分不变量被破坏（chunk_size={report['chunk_config']['chunk_size']} "
        f"chunk_overlap={report['chunk_config']['chunk_overlap']}）：\n"
        + _failures_text(report))
    assert report["invariant_failures"] == 0


def test_retrieval_golden_set_passes(golden_cases):
    """检索黄金集：当前生效参数下，所有用例必须命中期望结果。"""
    report = run_suite()
    assert report["ok"], (
        f"检索黄金集退步：{report['passed']}/{report['cases']} 通过\n"
        + _failures_text(report))


def test_chunk_size_400_is_still_safe(golden_cases):
    """策略边界：chunk_size=400 仍须全绿。

    这条不是随手挑的数字——sweep 实测出 400 是当前语料的安全下界：
    再往下（300/200）`11-薪资保密` 会失效，因为用户问"工资"而文档写"薪资"，
    文档级切分时两者同块互相兜底，块一小就断链。

    所以：**要缩小 chunk_size，先改文档用词或扩充黄金集**，别直接改。
    真的有意愿改边界时，连同这条断言一起更新，并写清原因。
    """
    report = run_suite(chunk_size=400, chunk_overlap=50, cases=golden_cases)
    assert report["ok"], (
        "chunk_size=400 已跌破检索安全边界：\n" + _failures_text(report))
