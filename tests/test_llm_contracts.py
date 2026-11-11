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

    async def _raw(prompt_text, variables, call_site, candidate_id, client=None,
                   raw=False):
        calls.append({"prompt": prompt_text, "call_site": call_site,
                      "variables": variables, "raw": raw})
        content = seq.pop(0) if seq else "{}"
        msg = AIMessage(content=content)
        return msg, {"model": "m", "model_served": "m", "base_url": "http://x",
                     "input_tokens": 10, "output_tokens": 5, "cost": 0.001,
                     "latency_ms": 10}

    return _raw, calls


class _StubLLM:
    """最小可用的模型桩：记录每次收到的 payload，按顺序吐出预设文本。

    刻意**不** mock `_invoke_raw`——修正式调用的模板 bug 就藏在那一层，
    mock 掉它等于把被测代码换成自己写的假货，测不出任何东西。

    带上 usage_metadata：不带的话成本会记 0，
    "修正调用有真实花费必须入账"这条就验不出来。
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def invoke(self, payload):
        self.seen.append(payload)
        return AIMessage(
            content=self.replies.pop(0),
            usage_metadata={"input_tokens": 30, "output_tokens": 20,
                            "total_tokens": 50},
        )


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


class TestRepairCallReallyRuns:
    """走真实 `_invoke_raw`（只桩模型），专门盯修正调用的模板问题。

    ## 为什么这组测试必须存在

    上面 `TestInvokeJsonContract` 全部把 `_invoke_raw` 整个 mock 掉了——
    于是"修正调用到底有没有真的发出去"根本没被验证。

    实测踩到的坑：修正提示词里嵌着**原输出的 JSON** 与**JSON 模板**，两者都带
    `{` `}`。`_invoke_raw` 原本把任何 prompt 都塞进 `PromptTemplate`
    （f-string 风格），花括号被当成占位符解析，直接抛

        Invalid format specifier in f-string template.
        Nested replacement fields are not allowed

    结果修正调用**一次都没成功过**：每次都记一行 failed、cost=0，
    然后退回原结果。任务照常跑完，埋点里也只有一行"failed"，
    看起来像"模型这次没修好"，而不是"修正机制根本没通"。
    实测 after 轮 7 次修正全部 failures，修正成功率 0/7。

    所以这里**不 mock `_invoke_raw`**，只桩模型，让模板真正跑一遍。
    """

    def test_repair_prompt_with_braces_is_sent_without_template(self, monkeypatch):
        """带花括号的修正提示词必须原样发送，不能被当成 f-string 模板"""
        stub = _StubLLM([
            json.dumps({"score": None, "matched_skills": [], "missing_skills": []}),
            json.dumps({"score": 77, "matched_skills": ["Go"], "missing_skills": []}),
        ])
        monkeypatch.setattr(llm_invoke, "get_llm", lambda: stub)
        mark = _log_mark()

        out = asyncio.run(llm_invoke.invoke_json(
            "请评估这份简历：{resume_text}", {"resume_text": "5 年 Python 经验"},
            {"score": 70}, "evaluate_skill_match"))

        # 首次不合规 -> 修正一次 -> 用修正结果
        assert out["score"] == 77, "修正结果没被采用，说明修正调用又失败了"
        assert len(stub.seen) == 2, f"应当正好两次调用，实际 {len(stub.seen)} 次"

        # 第一次走正常模板：PromptTemplate 渲染出来的是 StringPromptValue
        # （不是裸 str），占位符已经被替换成实际值。
        first = stub.seen[0]
        first_text = first.to_string() if hasattr(first, "to_string") else str(first)
        assert "5 年 Python 经验" in first_text, "正常的模板路径应当完成变量替换"
        assert "{resume_text}" not in first_text

        # 第二次是修正：**原样发送整段文本**，是裸 str，
        # 里面带 JSON 花括号也没被当占位符解析——这正是修复点。
        repair_payload = stub.seen[1]
        assert isinstance(repair_payload, str), \
            f"修正提示词必须原样发送，实际类型 {type(repair_payload).__name__}"
        assert "{" in repair_payload and "}" in repair_payload, \
            "修正提示词本身就含花括号，原样发送才对"

        logs = _logs_since(mark)
        repair = [l for l in logs if l.is_repair][-1]
        assert repair.status == "ok", f"修正调用仍然失败：{repair.error}"
        # 断言**用量**被记下来了，而不是"花费 > 0"：
        # 花费 = 用量 × 单价，而单价来自 `.env` 的配置。CI 里没有 `.env`，
        # 单价为 0，cost 必然是 0——拿它当断言等于在测配置文件，
        # 本地绿、CI 红，而且红了也说明不了代码有问题。
        # 代码真正负责的是"把这次调用的用量如实记下来，别漏账"。
        assert repair.input_tokens == 30 and repair.output_tokens == 20, \
            f"修正调用的用量没记下来：in={repair.input_tokens} out={repair.output_tokens}"
        assert repair.cost is not None, "花费字段至少要有值（0 也可以，取决于单价配置）"

    def test_repair_failure_is_recorded_with_real_cost(self, monkeypatch):
        """模型真的修不好时才记失败——并且要带上真实花费"""
        stub = _StubLLM([
            json.dumps({"score": None, "matched_skills": [], "missing_skills": []}),
            json.dumps({"score": "优秀", "matched_skills": [], "missing_skills": []}),
        ])
        monkeypatch.setattr(llm_invoke, "get_llm", lambda: stub)
        mark = _log_mark()

        out = asyncio.run(llm_invoke.invoke_json(
            "请评估这份简历：{resume_text}", {"resume_text": "5 年 Python 经验"},
            {"score": 70}, "evaluate_skill_match"))

        # 修不好就保留原结果，不本地编造
        assert out["score"] is None
        logs = _logs_since(mark)
        original = [l for l in logs if not l.is_repair][-1]
        repair = [l for l in logs if l.is_repair][-1]
        assert original.schema_valid is False
        assert original.schema_fixed is False
        # 这次失败是"模型给出的仍不合规"，不是"模板炸了"
        assert repair.status == "ok"
        # 同前：断言用量，不断言花费（花费取决于 `.env` 里的单价，CI 没有）
        assert repair.input_tokens == 30 and repair.output_tokens == 20
