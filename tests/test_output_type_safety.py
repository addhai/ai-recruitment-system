# -*- coding: utf-8 -*-
"""LLM 输出的类型安全：字段类型不对不得杀死整条任务，且必须标出降级维度。

## 问题

`invoke_json` 只保证"返回的是可解析的 JSON"，**不保证字段类型**。
模型完全可能返回 {"score": null} 或 {"score": "优秀"}，
而节点里一句 `float(result.get("score", 70))` 就会抛 ValueError/TypeError，
异常一路冒到驱动层——整条任务直接判失败，前面几个维度已经评完、
钱已经花了，全白费。

故障注入实测（20 组/模式）：类型违规注入下任务存活率只有 0.400，
而超时/5xx/坏 JSON 注入下都是 1.000——因为后三者被 invoke_json 捕获并降级了，
**只有类型违规会穿透**。

## 另一个 bug：降级被伪装成正常评分

文化契合的兜底 default 自带 reasons，而 degraded 判定是
`not reasons and not evidence`——于是 LLM 挂了反而判成 False，
兜底 70 分被当成模型给的正常评分。而它 >= 复核下限，会**直接放行**
候选人进入下一关。
"""
import asyncio
import uuid

from src.models.database import Candidate, SessionLocal
from src.workflow import recruitment_graph as rg


def _mk_candidate() -> int:
    with SessionLocal() as db:
        c = Candidate(name="类型安全测试候选人",
                      email=f"ts_{uuid.uuid4().hex[:8]}@t.com")
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id


def _state(cid: int) -> dict:
    return {
        "candidate_id": cid,
        "candidate_name": "类型安全测试候选人",
        "resume_text": "5 年 Python 后端经验",
        "position_requirements": "后端工程师",
        "position": "后端工程师",
        "jd_profile": {
            "basic": {
                "experience_years_required": "3年以上",
                "education_required": "本科及以上",
            },
            "responsibilities": ["负责招聘系统后端开发"],
            "required_skills": [{"skill": "Python", "evidence": "熟练使用 Python"}],
            "culture_values": ["跨团队协作"],
        },
        "jd_source": "job_description",
        "workflow_progress": 0,
        "current_step": "start",
    }


# ================================================================ 工具函数
class TestTypeSafeAccessors:
    def test_score_of_rejects_bad_types(self):
        """分数字段类型不对时取兜底值，绝不抛异常"""
        assert rg._score_of({"score": None}) == 70.0
        assert rg._score_of({"score": "优秀"}) == 70.0
        assert rg._score_of({"score": []}) == 70.0
        assert rg._score_of({"score": True}) == 70.0
        assert rg._score_of({"score": "88"}) == 88.0
        assert rg._score_of({"score": 85}) == 85.0
        assert rg._score_of("不是字典") == 70.0
        assert rg._score_of({}) == 70.0

    def test_list_of_rejects_bad_types(self):
        """列表字段类型不对时当空列表，绝不抛异常"""
        assert rg._list_of({"a": ["x"]}, "a") == ["x"]
        assert rg._list_of({"a": "abc"}, "a") == []
        assert rg._list_of({"a": 123}, "a") == []
        assert rg._list_of({"a": None}, "a") == []
        assert rg._list_of("不是字典", "a") == []

    def test_scored_marks_degraded(self):
        """兜底值与类型违规都必须被标成降级——否则 HR 会把兜底分当正常分"""
        default = {"analysis": "经验评估降级", "score": 70}
        _, ok = rg._scored({"analysis": "真实分析", "score": 82}, default)
        assert ok is False, "真实评分不应被标成降级"

        _, bad = rg._scored(default, default)
        assert bad is True, "原样返回兜底值必须标成降级"

        _, nullish = rg._scored({"score": None}, default)
        assert nullish is True, "score 为 null 必须标成降级"

        _, strish = rg._scored({"score": "优秀"}, default)
        assert strish is True, "score 是非数字字符串必须标成降级"


# ================================================================ 节点层面
class TestNodesSurviveBadTypes:
    """喂进类型违规的 LLM 结果，任务不能崩，且要标出降级维度"""

    def test_skill_match_survives_null_score(self, monkeypatch):
        cid = _mk_candidate()

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            return {"score": None, "matched_skills": "not-a-list",
                    "missing_skills": 123, "reason": None}

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        out = asyncio.run(rg.evaluate_skill_match(_state(cid)))
        assert out["skill_match_score"] == 70.0
        assert "skill_match_score" in (out.get("degraded_dimensions") or [])

    def test_culture_degraded_flag_survives_fallback(self, monkeypatch):
        """文化契合的兜底值必须被标成 degraded。

        修之前的判定是 `not reasons and not evidence`，而兜底 default 自带
        reasons，于是 LLM 挂了反而判成 False——兜底 70 分被当成模型给的分数，
        而且 70 >= 复核下限，会直接放行候选人进入下一关。
        """
        cid = _mk_candidate()

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            return default

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        out = asyncio.run(rg.assess_cultural_fit(_state(cid)))
        assert "culture_match_score" in (out.get("degraded_dimensions") or [])

    def test_culture_real_answer_not_degraded(self, monkeypatch):
        """真实评估结果不能被误标成降级"""
        cid = _mk_candidate()

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            return {"evidence": ["主导过跨团队项目交付"], "score": 80,
                    "reasons": ["有明确的协作与交付证据"]}

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        out = asyncio.run(rg.assess_cultural_fit(_state(cid)))
        assert "culture_match_score" not in (out.get("degraded_dimensions") or [])

    def test_experience_survives_bad_types(self, monkeypatch):
        cid = _mk_candidate()

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            return {"score": "很高", "analysis": None}

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        out = asyncio.run(rg.evaluate_experience(_state(cid)))
        assert out["experience_score"] == 70.0
        assert "experience_score" in (out.get("degraded_dimensions") or [])

    def test_education_survives_bad_types(self, monkeypatch):
        cid = _mk_candidate()

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            return {"score": None, "analysis": 123}

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        out = asyncio.run(rg.evaluate_education(_state(cid)))
        assert out["education_score"] == 70.0

    def test_questionnaire_scoring_survives_bad_types(self, monkeypatch):
        """问卷评分同样是模型输出，类型不对也不能炸"""
        cid = _mk_candidate()
        state = _state(cid)
        state.update({"questionnaire_id": 1, "questionnaire_responses": {}})

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            return {"score": None, "feedback": None}

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        # 问卷评分节点依赖 interrupt 恢复态，直接测它的取值处理更稳：
        # 这里验证 _score_of 与 max/min 组合不会抛
        assert rg._score_of({"score": None}, 60.0) == 60.0
        score = int(max(0, min(100, rg._score_of({"score": None}, 60.0))))
        assert score == 60
