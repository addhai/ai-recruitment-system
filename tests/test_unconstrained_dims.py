# -*- coding: utf-8 -*-
"""JD「不限制」维度测试：学历/年限/价值观设为无时不评分、不卡简历。

背景：JD 原文常常不写学历、年限或"我们看重什么"。此前这些空值会被
当成"按通用本科起评估"或退回通用价值观，等于凭空造出候选人不满足的要求
去扣分。改为：显式填"不限制"或原文没写 → 该维度不评分、不参与综合分、
文化门槛直接放行。
"""
import asyncio

import pytest

from src.workflow import recruitment_graph as rg
from src.services import job_parser as jp


def _state(cid, **extra):
    base = {
        "candidate_id": cid,
        "candidate_name": "不限制测试候选人",
        "resume_text": "Python 后端开发",
        "position": "工程师",
        "parsed_resume": {
            "skills": ["Python"],
            "experience": [],
            "projects": [{"name": "某项目"}],
            "education": [],
        },
        "jd_profile": {"basic": {}, "responsibilities": ["做后端"], "required_skills": [],
                       "preferred_skills": [], "culture_values": [], "tech_stack": []},
    }
    base.update(extra)
    return base


# ================================================================ 识别口径
class TestIsUnconstrained:
    @pytest.mark.parametrize("value", [
        None, "", "   ", "不限制", "无", "不限", "无要求", "不设限",
    ])
    def test_treated_as_unconstrained(self, value):
        assert rg.is_unconstrained(value) is True

    @pytest.mark.parametrize("value", ["本科及以上", "3年以上", "硕士优先"])
    def test_real_requirement_not_unconstrained(self, value):
        assert rg.is_unconstrained(value) is False

    def test_empty_list_unconstrained(self):
        assert rg.is_unconstrained([]) is True
        assert rg.is_unconstrained(["严谨负责"]) is False

    def test_jd_parser_shares_the_markers(self):
        """job_parser 与工作流必须认同一套标记词，否则保存的"不限制"读不出来"""
        assert jp.UNCONSTRAINED_MARKERS == rg.settings.UNCONSTRAINED_MARKERS


# ================================================================ 文化契合
class TestCultureUnconstrained:
    def _patch_llm(self, monkeypatch, score=40):
        called = {"n": 0}

        async def _fake(prompt, variables, default=None, *, call_site=None, **__):
            called["n"] += 1
            return {"evidence": [], "score": score, "reasons": ["r"]}
        monkeypatch.setattr(rg, "_llm_json", _fake)
        return called

    def test_empty_culture_values_skips_scoring(self, monkeypatch):
        from tests.test_db_actions import _mk_candidate
        called = self._patch_llm(monkeypatch)
        out = asyncio.run(rg.assess_cultural_fit(_state(_mk_candidate())))
        assert called["n"] == 0, "岗位未设价值观时不应再调用模型评分"
        assert out["culture_match_score"] is None
        assert out["culture_unconstrained"] is True
        assert "culture_match_score" in out["unconstrained_dimensions"]
        assert out["needs_review"] is False

    def test_unconstrained_marker_skips_scoring(self, monkeypatch):
        from tests.test_db_actions import _mk_candidate
        called = self._patch_llm(monkeypatch)
        state = _state(_mk_candidate())
        state["jd_profile"]["culture_values"] = ["不限制"]
        out = asyncio.run(rg.assess_cultural_fit(state))
        assert called["n"] == 0
        assert out["culture_match_score"] is None

    def test_gate_passes_when_unconstrained(self):
        """关键回归：不设价值观时文化门槛必须放行，不能把候选人卡死"""
        state = {"culture_match_score": None, "culture_unconstrained": True}
        assert rg.check_culture_pass(state) == "pass"

    def test_gate_rejects_when_merely_missing(self):
        """区分「岗位不设限」与「本该评却没评到」，后者仍保守判淘汰"""
        assert rg.check_culture_pass({"culture_match_score": None}) == "reject"

    def test_normal_scoring_still_works(self, monkeypatch):
        from tests.test_db_actions import _mk_candidate
        self._patch_llm(monkeypatch, score=78)
        state = _state(_mk_candidate())
        state["jd_profile"]["culture_values"] = ["严谨负责", "主动学习"]
        out = asyncio.run(rg.assess_cultural_fit(state))
        assert out["culture_match_score"] == 78
        assert out["culture_unconstrained"] is False
        assert "culture_match_score" not in out["unconstrained_dimensions"]


# ================================================================ 学历
class TestEducationUnconstrained:
    def test_unconstrained_skips_scoring(self, monkeypatch):
        from tests.test_db_actions import _mk_candidate
        called = {"n": 0}

        async def _fake(prompt, variables, default=None, *, call_site=None, **__):
            called["n"] += 1
            return {"analysis": "x", "score": 95}
        monkeypatch.setattr(rg, "_llm_json", _fake)

        out = asyncio.run(rg.evaluate_education(_state(_mk_candidate())))
        assert called["n"] == 0
        assert "education_score" not in out
        assert "education_score" in out["unconstrained_dimensions"]

    def test_explicit_marker_skips(self, monkeypatch):
        from tests.test_db_actions import _mk_candidate
        called = {"n": 0}

        async def _fake(prompt, variables, default=None, *, call_site=None, **__):
            called["n"] += 1
            return {"analysis": "x", "score": 95}
        monkeypatch.setattr(rg, "_llm_json", _fake)

        state = _state(_mk_candidate())
        state["jd_profile"]["basic"] = {"education_required": "不限制"}
        out = asyncio.run(rg.evaluate_education(state))
        assert called["n"] == 0

    def test_real_requirement_still_scored(self, monkeypatch):
        from tests.test_db_actions import _mk_candidate

        async def _fake(prompt, variables, default=None, *, call_site=None, **__):
            return {"analysis": "本科软件工程对口", "score": 92}
        monkeypatch.setattr(rg, "_llm_json", _fake)

        state = _state(_mk_candidate())
        state["jd_profile"]["basic"] = {"education_required": "本科及以上"}
        out = asyncio.run(rg.evaluate_education(state))
        assert out["education_score"] == 92


# ================================================================ 年限
class TestExperienceYearsUnconstrained:
    def _capture(self, monkeypatch):
        captured = {}

        async def _fake(prompt, variables, default=None, *, call_site=None, **__):
            captured.update(variables)
            return {"years_relevant": 0, "analysis": "按项目评估", "score": 80}
        monkeypatch.setattr(rg, "_llm_json", _fake)
        return captured

    def test_unconstrained_years_not_penalized(self, monkeypatch):
        """岗位不设年限 → 提示词里明确不按年限扣分，应届生不会被年限压分"""
        from tests.test_db_actions import _mk_candidate
        captured = self._capture(monkeypatch)
        out = asyncio.run(rg.evaluate_experience(_state(_mk_candidate())))
        assert "不设限制" in captured["jd_experience_req"]
        assert "不按年限扣分" in captured["jd_experience_req"]
        assert out["experience_score"] == 80

    def test_real_years_requirement_used(self, monkeypatch):
        from tests.test_db_actions import _mk_candidate
        captured = self._capture(monkeypatch)
        state = _state(_mk_candidate())
        state["jd_profile"]["basic"] = {"experience_years_required": "3年以上"}
        asyncio.run(rg.evaluate_experience(state))
        assert captured["jd_experience_req"] == "3年以上"


# ================================================================ 综合分
class TestUnconstrainedExcludedFromOverall:
    def test_unconstrained_dims_excluded(self):
        """未设限的维度不参与综合分，也不被当成 0 分拉低总分"""
        score, assessed = rg.compute_overall({
            "skill_match_score": 88,
            "experience_score": 80,
            "education_score": None,
            "culture_match_score": None,
        })
        assert "education_score" not in assessed
        assert "culture_match_score" not in assessed
        assert score == 84  # (88*.20 + 80*.15) / 0.35

    def test_unconstrained_never_triggers_hard_veto(self, monkeypatch):
        """未设限的问卷维度不能触发硬否决把分数封顶"""
        monkeypatch.setattr(rg, "_llm_json",
                            lambda *a, **k: _async_none())
        cid = _mk_candidate_safe()
        state = _state(cid, skill_match_score=100, experience_score=100,
                       education_score=100, questionnaire_score=None,
                       culture_match_score=None,
                       interview_scores=[{"round": 1, "score": 95},
                                         {"round": 2, "score": 95}])
        out = asyncio.run(rg.generate_hiring_decision(state))
        assert out["overall_score"] > 79, "未设限维度不应触发硬否决封顶"

    def test_unconstrained_dimension_not_passed_as_zero_to_llm(self, monkeypatch):
        """回归：未设限维度传 0 给决策模型，会被写成"教育0分"这类不存在的风险

        实测踩过：教育维度标了不限制、不参与评分，但决策提示词里仍是 0，
        模型据此输出"教育匹配为0分，需确认学历硬性要求"的风险项——
        而岗位本来就没设学历要求，这条风险是凭空来的。
        """
        captured = {}

        async def _capture(prompt, variables, default=None, *, call_site=None, **_):
            captured.update(variables)
            return {"decision": "推荐录用", "reasons": ["r"], "risks": []}

        monkeypatch.setattr(rg, "_llm_json", _capture)
        cid = _mk_candidate_safe()
        state = _state(cid,
                       skill_match_score=92, experience_score=92,
                       education_score=None, culture_match_score=78,
                       questionnaire_score=65,
                       interview_scores=[{"round": 1, "score": 86}],
                       unconstrained_dimensions=["education_score"])
        asyncio.run(rg.generate_hiring_decision(state))

        assert "未设限" in str(captured["education"]), \
            "未设限维度必须以文字形式传给模型，不能是 0"
        assert str(captured["education"]) != "0"
        # 其余维度仍是正常分数
        assert str(captured["skill"]) == "92"

    def test_dim_text_distinguishes_all_three_states(self):
        """未设限 / 未评估 / 真实分数，三种情况必须区分清楚"""
        assert "未设限" in rg._dim_text(None, "education_score", ["education_score"])
        assert "未评估" in rg._dim_text(None, "education_score", [])
        assert rg._dim_text(95, "education_score", []) == "95"


def _mk_candidate_safe():
    from tests.test_db_actions import _mk_candidate
    return _mk_candidate()


async def _async_none(*a, **k):
    return {"decision": "推荐录用", "reasons": ["r"], "risks": []}