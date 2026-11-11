# -*- coding: utf-8 -*-
"""四个评分维度并行化后的守门测试。

## 为什么并行化要单独守门

这四个维度互不依赖，原本是四条**串行边**。合成一个节点并行跑之后，
有三件事必须钉住，否则出了问题没人知道：

1. **四个维度的分都要有**——并行合并时如果覆盖写错了 key，
   某一个维度的分数会悄悄消失，综合分跟着算错；
2. **集合型 state 键要取并集**——`degraded_dimensions` 与
   `unconstrained_dimensions` 是四个节点基于同一份输入算出来的，
   直接覆盖只会留下最后一个维度的那一条；
3. **一个维度抛异常不能拖垮其余三个**——串行时它会杀死整条任务，
   并行时应该只丢这一个维度。
"""
import asyncio

import pytest

from src.workflow import recruitment_graph as rg


def _state() -> dict:
    return {
        "candidate_id": 1,
        "candidate_name": "并行测试候选人",
        "resume_text": "5 年 Python 后端经验",
        "position_requirements": "后端工程师",
        "position": "后端工程师",
        "jd_profile": {
            "basic": {"education_required": "本科及以上学历",
                      "experience_years_required": "3年以上"},
            "responsibilities": ["负责招聘系统后端开发"],
            "required_skills": [{"skill": "Python", "evidence": "熟练使用 Python"}],
            "culture_values": ["跨团队协作"],
        },
        "jd_source": "job_description",
        "workflow_progress": 10,
        "current_step": "extract_skills",
    }


class TestEvaluateDimensions:
    def test_all_four_dimensions_are_scored(self, monkeypatch):
        """四个维度的分必须都在——并行合并最容易在这里丢字段"""

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            if call_site == "evaluate_skill_match":
                return {"score": 82, "matched_skills": ["Python"], "missing_skills": []}
            if call_site == "evaluate_experience":
                return {"score": 74, "analysis": "年限达标"}
            if call_site == "evaluate_education":
                return {"score": 91, "analysis": "学历达标"}
            if call_site == "assess_cultural_fit":
                return {"score": 68, "evidence": ["有协作证据"], "reasons": []}
            return default

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        out = asyncio.run(rg.evaluate_dimensions(_state()))

        assert out["skill_match_score"] == 82
        assert out["experience_score"] == 74
        assert out["education_score"] == 91
        assert out["culture_match_score"] == 68
        assert out["current_step"] == "evaluate_dimensions"

    def test_degraded_dimensions_are_unioned(self, monkeypatch):
        """降级维度必须取并集：直接覆盖会只剩最后一个维度"""

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            # 每个维度都"挂了"（原样返回兜底值）
            return default

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        out = asyncio.run(rg.evaluate_dimensions(_state()))

        dims = out.get("degraded_dimensions") or []
        assert "skill_match_score" in dims
        assert "experience_score" in dims
        assert "education_score" in dims
        assert "culture_match_score" in dims

    def test_one_failing_dimension_does_not_kill_the_others(self, monkeypatch):
        """一个维度抛异常，其余三个照常出分"""

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            if call_site == "evaluate_experience":
                raise RuntimeError("注入：经验维度炸了")
            if call_site == "evaluate_skill_match":
                return {"score": 80, "matched_skills": [], "missing_skills": []}
            if call_site == "evaluate_education":
                return {"score": 88, "analysis": "学历达标"}
            if call_site == "assess_cultural_fit":
                return {"score": 66, "evidence": [], "reasons": []}
            return default

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        out = asyncio.run(rg.evaluate_dimensions(_state()))

        assert out["skill_match_score"] == 80
        assert out["education_score"] == 88
        assert out["culture_match_score"] == 66
        # 失败维度没有分数（不是 0 分，是"没评上"）
        assert out.get("experience_score") is None
        assert "evaluate_experience" in (out.get("failed_dimensions") or [])

    def test_unconstrained_dimensions_are_unioned(self, monkeypatch):
        """岗位未设限的维度清单也要取并集"""

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            return default

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        state = _state()
        # 学历与文化都不设限
        state["jd_profile"]["basic"]["education_required"] = "不限制"
        state["jd_profile"]["culture_values"] = []

        out = asyncio.run(rg.evaluate_dimensions(state))

        free = out.get("unconstrained_dimensions") or []
        assert "education_score" in free
        assert "culture_match_score" in free

    def test_detail_dicts_are_merged_not_overwritten(self, monkeypatch):
        """skills/experience/education 三份明细都要留下。

        串行布局下 `skill_match_details` 是累加的（后者 `{**旧值, ...}`）；
        并行后四个节点读同一份 base state，直接覆盖的话只剩最后一个。
        分数不受影响，但明细丢了，下游没法解释"这个维度为什么这么给分"。
        """

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            if call_site == "evaluate_skill_match":
                return {"score": 80, "matched_skills": ["Python"], "missing_skills": []}
            if call_site == "evaluate_experience":
                return {"score": 70, "analysis": "经验达标"}
            if call_site == "evaluate_education":
                return {"score": 90, "analysis": "学历达标"}
            if call_site == "assess_cultural_fit":
                return {"score": 60, "evidence": [], "reasons": []}
            return default

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        out = asyncio.run(rg.evaluate_dimensions(_state()))

        details = out.get("skill_match_details") or {}
        assert "experience_analysis" in details, "经验明细丢了"
        assert "education_analysis" in details, "教育明细丢了"
        # 技能节点写的是 result 本身（含 matched_skills）
        assert details.get("matched_skills") == ["Python"], "技能明细丢了"


class TestGraphAssembly:
    def test_graph_compiles_with_parallel_node(self):
        graph = rg.build_recruitment_graph()
        nodes = graph.get_graph().nodes
        assert "evaluate_dimensions" in nodes
        # 老节点仍然注册（老 checkpoint 兼容），只是不再有入边
        assert "evaluate_skill_match" in nodes
        assert "assess_cultural_fit" in nodes

    def test_scoring_chain_goes_through_parallel_node(self):
        """入口必须接到并行节点，而不是旧的串行边"""
        graph = rg.build_recruitment_graph()
        edges = {(e.source, e.target) for e in graph.get_graph().edges}
        assert ("extract_skills", "evaluate_dimensions") in edges
        # 旧的四条串行边不能再存在
        assert ("evaluate_skill_match", "evaluate_experience") not in edges
        assert ("evaluate_education", "assess_cultural_fit") not in edges

    def test_parallel_node_has_outgoing_edge(self):
        """并行节点**必须**有出边，否则流程在评分后直接走到图尽头。

        这是一个真实踩过的坑：把评分链从
        `extract_skills -> skill -> expr -> edu -> culture -> questionnaire`
        改成 `extract_skills -> evaluate_dimensions` 时，
        文化关卡的 `add_conditional_edges` 还挂在旧的 `assess_cultural_fit` 上。
        结果：四个维度都算出了分、调用次数也对得上，
        但流程停在评分之后，问卷和面试一次都不跑（实测单任务从 13 次调用
        掉到 5 次、决策变成"待定"）。图能编译、不报错、分数看着也正常，
        只有"调用数少了一大截"能暴露它——所以这条边必须被断言钉住。
        """
        graph = rg.build_recruitment_graph()
        edges = [(e.source, e.target) for e in graph.get_graph().edges]
        # 注意要排除 __end__：图尽头也是一条"出边"，
        # 只断言"有出边"的话，评分后直接走到 END 也会被判定为通过——
        # 那正是这个 bug 的实际形态（evaluate_dimensions -> __end__）。
        outgoing = [t for s, t in edges
                    if s == "evaluate_dimensions" and t != "__end__"]
        assert outgoing, "evaluate_dimensions 之后没有真实节点，流程会在评分后终止"

    def test_culture_gate_is_attached_to_parallel_node(self):
        """文化关卡必须挂在并行节点上，不能还挂在已无入边的旧节点上"""
        graph = rg.build_recruitment_graph()
        edges = {(e.source, e.target) for e in graph.get_graph().edges}
        reachable = {t for s, t in edges if s == "evaluate_dimensions"}
        assert "questionnaire_stage" in reachable or "reject_to_pool" in reachable, \
            "文化关卡没接到并行节点的下游"

    def test_old_culture_node_has_no_incoming_edge(self):
        """旧的 assess_cultural_fit 只做兼容注册，不应再被任何边指到"""
        graph = rg.build_recruitment_graph()
        edges = [(e.source, e.target) for e in graph.get_graph().edges]
        incoming = [s for s, t in edges if t == "assess_cultural_fit"]
        assert not incoming, f"assess_cultural_fit 还有入边：{incoming}"
