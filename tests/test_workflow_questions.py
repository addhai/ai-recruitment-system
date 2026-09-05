# -*- coding: utf-8 -*-
"""面试题生成单测：LLM 出题的降级、清洗、分轮次侧重。

LLM 调用通过 monkeypatch _llm_json 模拟，不发网络请求。
异步逻辑用 asyncio.run 同步驱动（与项目其他测试风格一致）。
"""
import asyncio

import pytest

from src.workflow import recruitment_graph as rg


def _state():
    return {
        "candidate_id": 1,
        "position": "高级Python工程师",
        "parsed_resume": {"skills": ["Python", "React"], "experience": "5年后端"},
    }


def _gen(state, round_no):
    return asyncio.run(rg.generate_interview_questions(state, round_no))


def _patch_llm(monkeypatch, return_value="__default__"):
    """return_value="__default__" 时模拟 LLM 失败的真实行为：_llm_json 返回 default"""
    async def _fake(prompt, variables, default=None):
        return default if return_value == "__default__" else return_value
    monkeypatch.setattr(rg, "_llm_json", _fake)


@pytest.mark.parametrize("round_no", [1, 2, 3])
def test_fallback_when_llm_returns_default(monkeypatch, round_no):
    # LLM 失败时 _llm_json 按既有约定返回 default（分轮次兜底题库）
    _patch_llm(monkeypatch)
    questions = _gen(_state(), round_no)
    assert len(questions) == 5
    for q in questions:
        assert set(q.keys()) == {"question", "focus"}
        assert q["question"].strip() and q["focus"].strip()


def test_three_rounds_have_distinct_focus(monkeypatch):
    _patch_llm(monkeypatch)
    s1 = {q["question"] for q in _gen(_state(), 1)}
    s2 = {q["question"] for q in _gen(_state(), 2)}
    s3 = {q["question"] for q in _gen(_state(), 3)}
    assert s1.isdisjoint(s2) and s2.isdisjoint(s3) and s1.isdisjoint(s3)
    # 第二轮 STAR 行为题，第三轮 HR 题，侧重可辨
    q2 = _gen(_state(), 2)
    q3 = _gen(_state(), 3)
    assert any("STAR" in q["focus"] for q in q2)
    assert any("薪资" in q["question"] or "职业" in q["question"] for q in q3)


def test_malformed_llm_output_padded_to_five(monkeypatch):
    broken = {"questions": [
        {"question": "定制题1", "focus": "考察点1"},
        {"focus": "缺question字段，应丢弃"},
        "不是字典，应丢弃",
        {"question": "  ", "focus": "空白题，应丢弃"},
    ]}
    _patch_llm(monkeypatch, return_value=broken)
    questions = _gen(_state(), 1)
    assert len(questions) == 5
    assert questions[0]["question"] == "定制题1"
    assert questions[0]["focus"] == "考察点1"
    assert all(q["question"].strip() for q in questions[1:])


def test_valid_llm_output_used_directly(monkeypatch):
    custom = {"questions": [
        {"question": f"定制题目{i}", "focus": f"考察点{i}"} for i in range(5)
    ]}
    _patch_llm(monkeypatch, return_value=custom)
    questions = _gen(_state(), 2)
    assert len(questions) == 5
    assert questions[3]["question"] == "定制题目3"


def test_non_dict_llm_result_falls_back(monkeypatch):
    _patch_llm(monkeypatch, return_value=["奇怪的列表"])
    questions = _gen(_state(), 1)
    assert len(questions) == 5
    assert all("question" in q for q in questions)
