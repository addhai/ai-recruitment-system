# -*- coding: utf-8 -*-
"""基线对比逻辑单测。

`compare_with_baseline` 是"换模型做回归"里唯一会给出"退步/没退步"结论的地方，
它的判定分两类且性质不同：断言退步是硬的（计入退出码），分数漂移是软的
（只提示）。这两类一旦混起来，要么天天误报，要么漏报真退步。

这里用合成报告验证判定逻辑，不调用任何 LLM——纯函数，秒级完成。
"""
from tests.eval.run_eval import compare_with_baseline


def _prov(model="deepseek-flash", code="code-A", config="cfg-A", chunk=None):
    return {
        "fingerprint": f"{code}-{config}",
        "code_digest": code,
        "config_digest": config,
        "model": model,
        "embedding_model": "BAAI/bge-large-zh-v1.5",
        "kb_chunk": chunk or {"chunk_size": 500, "chunk_overlap": 50},
    }


def _report(provenance, cases, dist=None):
    return {
        "provenance": provenance,
        "summary": {"score_distribution": dist or {}, "decisions": {}, "cases": len(cases),
                    "passed": sum(1 for c in cases if c["passed"]),
                    "failed": sum(1 for c in cases if not c["passed"]), "repeat": 1},
        "results": cases,
    }


def _case(name, assertions):
    """assertions: [(kind, ok), ...]"""
    return {
        "name": name,
        "passed": all(ok for _, ok in assertions),
        "runs": [{"run": 1, "metrics": {}, "assertions": [
            {"kind": k, "ok": ok, "detail": ""} for k, ok in assertions]}],
    }


class TestAssertionRegressions:
    def test_detects_assertion_regression(self):
        baseline = _report(_prov(), [_case("A", [("decision_shape", True)])])
        current = _report(_prov(), [_case("A", [("decision_shape", False)])])
        diff = compare_with_baseline(current, baseline)
        assert diff["regressions"], "断言从 PASS 变 FAIL 必须被判为退步"
        assert diff["regressions"][0]["case"] == "A"
        assert diff["regressions"][0]["kind"] == "decision_shape"
        assert diff["ok"] is False

    def test_detects_fixed_assertion(self):
        baseline = _report(_prov(), [_case("A", [("decision_shape", False)])])
        current = _report(_prov(), [_case("A", [("decision_shape", True)])])
        diff = compare_with_baseline(current, baseline)
        assert diff["fixed"], "基线失败、本次通过应记为修复项"
        assert diff["regressions"] == []
        assert diff["ok"] is True

    def test_no_change_is_ok(self):
        cases = [_case("A", [("decision_shape", True)])]
        diff = compare_with_baseline(_report(_prov(), cases), _report(_prov(), cases))
        assert diff["ok"] is True
        assert diff["regressions"] == []
        assert diff["fixed"] == []

    def test_repeat_any_failure_counts_as_failure(self):
        """多次 repeat 里只要有一次不通过，这条用例就没通过。"""
        baseline = _report(_prov(), [_case("A", [("decision_shape", True)])])
        cur_case = _case("A", [("decision_shape", True)])
        cur_case["runs"].append({"run": 2, "metrics": {}, "assertions": [
            {"kind": "decision_shape", "ok": False, "detail": ""}]})
        diff = compare_with_baseline(_report(_prov(), [cur_case]), baseline)
        assert diff["regressions"], "repeat 中任一次失败都应算退步"


class TestProvenanceChanges:
    def test_model_swap_is_detected_as_config_change_only(self):
        baseline = _report(_prov(model="deepseek-flash", config="cfg-A"), [])
        current = _report(_prov(model="deepseek-v4-pro", config="cfg-B"), [])
        diff = compare_with_baseline(current, baseline)
        assert not diff["comparable"]
        joined = " ".join(diff["provenance_changes"])
        assert "deepseek-flash" in joined and "deepseek-v4-pro" in joined
        assert "源码口径已变" not in joined, "换模型不该被误报成提示词也改了"

    def test_code_change_is_detected_separately(self):
        """提示词被一起改了 —— 必须单独报出来，否则分数差异无法归因。"""
        baseline = _report(_prov(model="deepseek-flash", code="code-A", config="cfg-A"), [])
        current = _report(_prov(model="deepseek-v4-pro", code="code-B", config="cfg-B"), [])
        diff = compare_with_baseline(current, baseline)
        joined = " ".join(diff["provenance_changes"])
        assert "源码口径已变" in joined
        assert "运行时配置口径已变" in joined

    def test_chunk_change_is_detected(self):
        baseline = _report(_prov(chunk={"chunk_size": 500, "chunk_overlap": 50}), [])
        current = _report(_prov(chunk={"chunk_size": 300, "chunk_overlap": 50}), [])
        diff = compare_with_baseline(current, baseline)
        assert "知识库切分参数已变" in " ".join(diff["provenance_changes"])

    def test_identical_provenance_is_comparable(self):
        diff = compare_with_baseline(_report(_prov(), []), _report(_prov(), []))
        assert diff["comparable"] is True
        assert diff["provenance_changes"] == []


class TestScoreDrift:
    def _dist(self, overall):
        return {"overall": {"n": 1, "min": overall, "max": overall,
                            "median": overall, "spread": 0}}

    def test_large_drift_is_reported_but_not_a_failure(self):
        baseline = _report(_prov(), [], dist=self._dist(80))
        current = _report(_prov(), [], dist=self._dist(95))
        diff = compare_with_baseline(current, baseline, drift_threshold=10)
        assert diff["score_drift"], "中位数漂移超阈值应被报告"
        assert diff["score_drift"][0]["delta"] == 15
        assert diff["ok"] is True, "分数漂移是软信号，不得计入失败"

    def test_small_drift_is_not_reported(self):
        baseline = _report(_prov(), [], dist=self._dist(80))
        current = _report(_prov(), [], dist=self._dist(83))
        diff = compare_with_baseline(current, baseline, drift_threshold=10)
        assert diff["score_drift"] == []

    def test_drift_threshold_is_configurable(self):
        baseline = _report(_prov(), [], dist=self._dist(80))
        current = _report(_prov(), [], dist=self._dist(85))
        diff = compare_with_baseline(current, baseline, drift_threshold=3)
        assert len(diff["score_drift"]) == 1


class TestCaseSetChanges:
    def test_added_and_removed_cases_are_reported(self):
        baseline = _report(_prov(), [_case("A", [("decision_shape", True)])])
        current = _report(_prov(), [_case("B", [("decision_shape", True)])])
        diff = compare_with_baseline(current, baseline)
        assert diff["added_cases"] == ["B"]
        assert diff["removed_cases"] == ["A"]
