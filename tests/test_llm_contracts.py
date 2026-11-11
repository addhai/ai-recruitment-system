# -*- coding: utf-8 -*-
"""输出契约的守门测试。

## 为什么要有这组测试

`invoke_json` 原本只保证"返回的是可解析的 JSON"，不保证字段齐全、类型正确。
模型返回 `{"score": null}` 时，这次调用在埋点里是 `status=ok / degraded=false`，
节点层兜底成 70 分 —— **埋点完全看不出这次调用不合规**。

注入实验（n=120）证明这类违规是唯一会穿透到驱动层的故障：
`bad_schema` 模式任务存活率只有 0.400，而超时/5xx/坏 JSON 都是 1.000
（后三者被 invoke_json 捕获并降级了）。

所以这里要钉住三件事：
1. 校验器能认出各种形状违规；
2. 不合规时确实会发起修正调用，且修好了就用修正结果；
3. 修正调用是真实请求，**必须单独埋点**——否则成本页会漏算这部分花费。
"""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from src.services import llm_contracts
from src.services import llm_invoke
from src.services.llm_contracts import Contract, FieldSpec


# ================================================================ 校验器
class TestContractValidator:
    def test_detects_missing_field(self):
        c = Contract("x", (FieldSpec("score", "number"),))
        assert c.validate({}) == ["score: 缺失（期望数字）"]
        assert c.validate({"score": 80}) == []

    def test_detects_wrong_type(self):
        c = Contract("x", (FieldSpec("score", "number"),))
        errs = c.validate({"score": None})
        assert errs and "score" in errs[0]
        errs = c.validate({"score": "优秀"})
        assert errs and "score" in errs[0]

    def test_bool_is_not_a_number(self):
        """True 在 Python 里是 int 的子类，但它是布尔不是分数。"""
        c = Contract("x", (FieldSpec("score", "number"),))
        assert c.validate({"score": True}) != []

    def test_range_check(self):
        c = Contract("x", (FieldSpec("score", "number", minimum=0, maximum=100),))
        assert c.validate({"score": 101}) != []
        assert c.validate({"score": -1}) != []
        assert c.validate({"score": 100}) == []

    def test_list_and_dict(self):
        c = Contract("x", (FieldSpec("a", "list"), FieldSpec("b", "dict")))
        assert c.validate({"a": [], "b": {}}) == []
        assert c.validate({"a": 123, "b": {}}) != []
        assert c.validate({"a": [], "b": "x"}) != []

    def test_list_of_dict_requires_item_keys(self):
        c = Contract("x", (FieldSpec("q", "list[dict]", item_keys=("question",)),))
        assert c.validate({"q": [{"question": "a"}]}) == []
        assert c.validate({"q": [{"text": "a"}]}) != []
        assert c.validate({"q": ["a"]}) != []

    def test_optional_field_may_be_absent(self):
        c = Contract("x", (FieldSpec("a", "list", required=False),))
        assert c.validate({}) == []

    def test_non_dict_is_rejected(self):
        c = Contract("x", (FieldSpec("a", "list"),))
        assert c.validate([1, 2]) != []

    def test_choices_are_checked(self):
        c = Contract("x", (FieldSpec("d", "string", choices=("录用", "待定")),))
        assert c.validate({"d": "录用"}) == []
        assert c.validate({"d": "不知道"}) != []


class TestContractTable:
    """契约表必须覆盖所有会产生结构化输出的 call_site"""

    def test_all_registered_sites_have_contract(self):
        for site in ("parse_resume", "extract_skills", "evaluate_skill_match",
                     "evaluate_experience", "evaluate_education",
                     "assess_cultural_fit", "questionnaire_stage",
                     "score_questionnaire", "generate_interview_questions",
                     "generate_hiring_decision", "parse_job_description"):
            assert llm_contracts.contract_for(site) is not None, site

    def test_unknown_site_has_no_contract(self):
        assert llm_contracts.contract_for("knowledge_qa") is None

    def test_decision_contract_allows_four_values(self):
        c = llm_contracts.contract_for("generate_hiring_decision")
        spec = [f for f in c.fields if f.name == "decision"][0]
        assert "推荐录用" in spec.choices
        assert "待人工复核" in spec.choices


# ================================================================ invoke_json 接线
def _fake_raw(payloads, calls=None):
    """造一个假的 _invoke_raw，按顺序返回给定 JSON 文本。"""
    seq = list(payloads)
    calls = calls if calls is not None else []

    async def _raw(prompt_text, variables, call_site, candidate_id, client=None):
        calls.append({"prompt": prompt_text, "call_site": call_site,
                      "variables": variables})
        content = seq.pop(0) if seq else "{}"
        msg = AIMessage(content=content)
        return msg, {"model": "m", "model_served": "m", "base_url": "http://x",
                     "input_tokens": 10, "output_tokens": 5, "cost": 0.001,
                     "latency_ms": 10}

    return _raw, calls


def _log_mark():
    """当前已写入的最大日志 id。

    测试之间共用同一个临时库，所以必须按 id 切片，
    否则上一个用例写的修正日志会被当成本次的结果。
    """
    from src.models.database import LLMCallLog, SessionLocal
    with SessionLocal() as db:
        row = db.query(LLMCallLog).order_by(LLMCallLog.id.desc()).first()
        return row.id if row else 0


def _logs_since(mark):
    from src.models.database import LLMCallLog, SessionLocal
    with SessionLocal() as db:
        return (db.query(LLMCallLog)
                .filter(LLMCallLog.id > mark)
                .order_by(LLMCallLog.id).all())


class TestInvokeJsonContract:
    def test_valid_response_is_recorded_as_valid(self, monkeypatch):
        mark = _log_mark()
        raw, calls = _fake_raw([json.dumps(
            {"score": 82, "matched_skills": ["Python"], "missing_skills": []})])
        monkeypatch.setattr(llm_invoke, "_invoke_raw", raw)

        out = asyncio.run(llm_invoke.invoke_json(
            "p", {}, {"score": 70}, "evaluate_skill_match"))

        assert out["score"] == 82
        assert len(calls) == 1, "合规的调用不该触发修正调用"
        log = _logs_since(mark)[-1]
        assert log.schema_valid is True
        assert log.schema_fixed is None
        assert log.is_repair is False

    def test_invalid_response_triggers_one_repair(self, monkeypatch):
        """首次不合规 → 修正一次 → 修好了就用修正结果"""
        mark = _log_mark()
        raw, calls = _fake_raw([
            json.dumps({"score": None, "matched_skills": [], "missing_skills": []}),
            json.dumps({"score": 77, "matched_skills": ["Go"], "missing_skills": []}),
        ])
        monkeypatch.setattr(llm_invoke, "_invoke_raw", raw)

        out = asyncio.run(llm_invoke.invoke_json(
            "p", {}, {"score": 70}, "evaluate_skill_match"))

        assert len(calls) == 2, "应当正好发起一次修正调用"
        assert out["score"] == 77, "应当用修正后的结果"
        assert "缺失" in calls[1]["prompt"] or "期望" in calls[1]["prompt"], \
            "修正提示词必须带上错误清单"

        logs = _logs_since(mark)
        original = [l for l in logs if not l.is_repair][-1]
        repair = [l for l in logs if l.is_repair][-1]
        assert original.schema_valid is False
        assert original.schema_fixed is True
        assert original.schema_errors, "首次错在哪必须留下来"
        assert repair.schema_valid is True

    def test_repair_failure_keeps_original_result(self, monkeypatch):
        """修正也没修好时，返回原结果（节点层再兜底），且如实标记"""
        mark = _log_mark()
        raw, calls = _fake_raw([
            json.dumps({"score": None, "matched_skills": [], "missing_skills": []}),
            json.dumps({"score": "优秀", "matched_skills": [], "missing_skills": []}),
        ])
        monkeypatch.setattr(llm_invoke, "_invoke_raw", raw)

        out = asyncio.run(llm_invoke.invoke_json(
            "p", {}, {"score": 70}, "evaluate_skill_match"))

        assert len(calls) == 2
        # 修不好就保留**原结果**，不本地编造，也不拿修正结果冒充好的
        assert out["score"] is None
        logs = _logs_since(mark)
        original = [l for l in logs if not l.is_repair][-1]
        assert original.schema_valid is False
        assert original.schema_fixed is False

    def test_repair_can_be_disabled(self, monkeypatch):
        """开关关掉时只记录不合规，不发起修正调用"""
        from src.config import settings
        mark = _log_mark()
        monkeypatch.setattr(settings, "LLM_SCHEMA_REPAIR_ENABLED", False)
        raw, calls = _fake_raw([json.dumps(
            {"score": None, "matched_skills": [], "missing_skills": []})])
        monkeypatch.setattr(llm_invoke, "_invoke_raw", raw)

        out = asyncio.run(llm_invoke.invoke_json(
            "p", {}, {"score": 70}, "evaluate_skill_match"))

        assert len(calls) == 1, "关掉后不该有修正调用"
        assert out["score"] is None
        log = [l for l in _logs_since(mark) if not l.is_repair][-1]
        assert log.schema_valid is False
        assert log.schema_fixed is None

    def test_repair_call_is_logged_separately(self, monkeypatch):
        """修正调用是真实请求，必须单独记一行，否则成本页会漏算"""
        mark = _log_mark()
        raw, calls = _fake_raw([
            json.dumps({"score": None, "matched_skills": [], "missing_skills": []}),
            json.dumps({"score": 77, "matched_skills": [], "missing_skills": []}),
        ])
        monkeypatch.setattr(llm_invoke, "_invoke_raw", raw)

        asyncio.run(llm_invoke.invoke_json(
            "p", {}, {"score": 70}, "evaluate_skill_match"))

        logs = _logs_since(mark)
        assert len(logs) >= 2
        repairs = [l for l in logs if l.is_repair]
        assert len(repairs) == 1
        assert repairs[0].cost > 0, "修正调用有真实花费，必须记上"
        assert repairs[0].call_site == "evaluate_skill_match"

    def test_site_without_contract_is_not_validated(self, monkeypatch):
        """没登记契约的 call_site 不做校验（不能凭空判它不合规）"""
        mark = _log_mark()
        raw, calls = _fake_raw([json.dumps({"whatever": 1})])
        monkeypatch.setattr(llm_invoke, "_invoke_raw", raw)

        out = asyncio.run(llm_invoke.invoke_json("p", {}, {}, "knowledge_qa"))

        assert len(calls) == 1
        assert out == {"whatever": 1}
        assert _logs_since(mark)[-1].schema_valid is None
