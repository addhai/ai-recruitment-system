# -*- coding: utf-8 -*-
"""工作流节点单测：评估节点降级、条件路由、终局沉淀、综合分加权与硬性否决。

LLM 调用 monkeypatch _llm_json；interrupt 挂起 monkeypatch 为直接返回人工事件，
从而在不启动 LangGraph 运行时的前提下覆盖"恢复后"的评分与回写逻辑。
"""
import asyncio

import pytest

from src.models.database import SessionLocal, Candidate, Interview, TalentPool
from src.workflow import recruitment_graph as rg
from tests.test_db_actions import _mk_candidate


def _state(cid, **extra):
    base = {
        "candidate_id": cid,
        "candidate_name": "节点测试候选人",
        "resume_text": "Python 后端 5 年，主导过交易系统重构",
        "position_requirements": "精通 Python、Redis、MySQL",
        "position": "高级Python工程师",
        "parsed_resume": {"skills": ["Python", "Redis"], "experience": [{"years": 5}], "education": [{"degree": "本科"}]},
    }
    base.update(extra)
    return base


def _patch_llm(monkeypatch, return_value="__default__"):
    async def _fake(prompt, variables, default=None):
        return default if return_value == "__default__" else return_value
    monkeypatch.setattr(rg, "_llm_json", _fake)


def _patch_interrupt(monkeypatch, payload):
    """把挂起点替换为直接返回人工事件（模拟 LangGraph resume）"""
    monkeypatch.setattr(rg, "interrupt", lambda v: payload)


# ---------------------------------------------------------------- 阶段一/二
def test_parse_resume_uses_cached_parsed(monkeypatch):
    # 已有解析结果时应直接复用，不再调 LLM
    called = {"n": 0}

    async def _fake_llm(*a, **kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr(rg, "_llm_json", _fake_llm)
    cid = _mk_candidate()
    from src.workflow import db_actions as da
    da.save_resume_parsed(cid, {"skills": ["Go"], "name": "缓存测试"})
    out = asyncio.run(rg.parse_resume(_state(cid)))
    assert out["parsed_resume"]["skills"] == ["Go"]
    assert called["n"] == 0
    assert out["current_step"] == "parse_resume"


def test_parse_resume_llm_fallback(monkeypatch):
    _patch_llm(monkeypatch)
    cid = _mk_candidate()
    out = asyncio.run(rg.parse_resume(_state(cid)))
    assert "LLM不可用" in out["parsed_resume"]["skills"][0]


def test_extract_skills_merges(monkeypatch):
    _patch_llm(monkeypatch, {"skills_technical": ["Python", "MySQL"], "skills_soft": ["沟通"], "domain_knowledge": ["电商"]})
    cid = _mk_candidate()
    out = asyncio.run(rg.extract_skills(_state(cid)))
    assert "MySQL" in out["parsed_resume"]["skills_technical"]
    assert out["current_step"] == "extract_skills"


@pytest.mark.parametrize("node,step", [
    (rg.evaluate_skill_match, "evaluate_skill_match"),
    (rg.evaluate_experience, "evaluate_experience"),
    (rg.evaluate_education, "evaluate_education"),
    (rg.assess_cultural_fit, "assess_cultural_fit"),
])
def test_evaluate_nodes_llm_scoring(monkeypatch, node, step):
    _patch_llm(monkeypatch, {"score": 88, "analysis": "测试分析", "evidence": ["x"], "reasons": ["r"]})
    cid = _mk_candidate()
    out = asyncio.run(node(_state(cid)))
    assert out["current_step"] == step
    assert out["workflow_progress"] > 0


def test_evaluate_nodes_fallback_score_70(monkeypatch):
    _patch_llm(monkeypatch)  # LLM 失败 → 默认 70
    cid = _mk_candidate()
    out = asyncio.run(rg.evaluate_skill_match(_state(cid)))
    assert out["skill_match_score"] == 70


# ---------------------------------------------------------------- 条件路由
def test_check_culture_pass_threshold():
    assert rg.check_culture_pass({"culture_match_score": 60}) == "pass"
    assert rg.check_culture_pass({"culture_match_score": 59.9}) == "reject"
    assert rg.check_culture_pass({}) == "reject"


def test_check_questionnaire_pass_threshold():
    assert rg.check_questionnaire_pass({"questionnaire_score": 60}) == "pass"
    assert rg.check_questionnaire_pass({"questionnaire_score": 30}) == "reject"


def test_make_interview_check():
    check = rg.make_interview_check(2)
    assert check({"interview_scores": [{"round": 2, "score": 70}]}) == "pass"
    assert check({"interview_scores": [{"round": 2, "score": 69}]}) == "reject"
    assert check({"interview_scores": []}) == "reject"  # 没有该轮分数视为未通过


def test_check_final_decision_routing():
    assert rg.check_final_decision({"final_decision": "推荐录用"}) == "hire"
    assert rg.check_final_decision({"final_decision": "不推荐"}) == "reject"
    assert rg.check_final_decision({"final_decision": "拒绝录用"}) == "reject"
    assert rg.check_final_decision({"final_decision": "待定"}) == "pool"
    assert rg.check_final_decision({}) == "pool"


# ---------------------------------------------------------------- 问卷挂起→评分
def test_questionnaire_stage_scores_real_answers(monkeypatch):
    _patch_llm(monkeypatch, {"score": 82, "feedback": "答案扎实"})
    _patch_interrupt(monkeypatch, {"responses": {"q1": "GIL是全局解释器锁", "q2": "用Redis做缓存"}})
    cid = _mk_candidate()
    out = asyncio.run(rg.questionnaire_stage(_state(cid)))
    assert out["questionnaire_score"] == 82
    assert out["current_step"] == "questionnaire_scored"

    from src.workflow import db_actions as da
    with SessionLocal() as db:
        from src.models.database import QuestionnaireResponse
        resp = db.query(QuestionnaireResponse).filter(QuestionnaireResponse.candidate_id == cid).first()
        assert resp is not None and resp.score == 82


def test_questionnaire_stage_fallback_by_completeness(monkeypatch):
    """LLM 评分失败时按作答完整度给 60-90 分"""
    _patch_llm(monkeypatch)  # 返回 default
    _patch_interrupt(monkeypatch, {"responses": {"q1": "答了", "q2": "", "q3": "也答了"}})
    cid = _mk_candidate()
    out = asyncio.run(rg.questionnaire_stage(_state(cid)))
    # 3 题答 2 → 60 + 30*2/3 = 80
    assert out["questionnaire_score"] == 80


# ---------------------------------------------------------------- 面试节点
def test_interview_node_records_real_score(monkeypatch):
    _patch_llm(monkeypatch)
    _patch_interrupt(monkeypatch, {"score": 85, "feedback": "技术扎实", "notes": "建议进二面"})
    cid = _mk_candidate()
    node = rg.make_interview_node(1, 58, 63)
    out = asyncio.run(node(_state(cid)))
    assert out["interview_scores"] == [{"round": 1, "score": 85}]
    assert out["workflow_progress"] == 63
    with SessionLocal() as db:
        iv = db.query(Interview).filter(Interview.candidate_id == cid, Interview.round == 1).first()
        assert iv.status == "completed" and iv.score == 85
        assert iv.questions is not None and len(iv.questions) == 5  # 兜底题库写入


def test_interview_node_replay_uses_existing_score(monkeypatch):
    """重放场景：面试已标记完成时直接读回分数，不再挂起"""
    _patch_llm(monkeypatch)
    cid = _mk_candidate()
    from src.workflow import db_actions as da
    info = da.schedule_interview(cid, 1, "岗位")
    da.complete_interview_record(info["interview_id"], score=91, feedback="已录入过")
    # 不 patch interrupt：若节点试图挂起会因无 LangGraph 上下文抛错
    node = rg.make_interview_node(1, 58, 63)
    out = asyncio.run(node(_state(cid)))
    assert out["interview_scores"][0]["score"] == 91


def test_interview_node_accumulates_rounds(monkeypatch):
    _patch_llm(monkeypatch)
    _patch_interrupt(monkeypatch, {"score": 75, "feedback": "ok"})
    cid = _mk_candidate()
    node2 = rg.make_interview_node(2, 69, 74)
    out = asyncio.run(node2(_state(cid, interview_scores=[{"round": 1, "score": 88}])))
    assert [s["round"] for s in out["interview_scores"]] == [1, 2]


# ---------------------------------------------------------------- 综合评审
def test_hiring_decision_weighted_score(monkeypatch):
    _patch_llm(monkeypatch)  # LLM 失败 → 用规则档位
    cid = _mk_candidate()
    state = _state(cid,
                   skill_match_score=90, experience_score=85, education_score=80,
                   culture_match_score=75, questionnaire_score=88,
                   interview_scores=[{"round": 1, "score": 90}, {"round": 2, "score": 85}, {"round": 3, "score": 80}])
    out = asyncio.run(rg.generate_hiring_decision(state))
    # 面试均分 85：85*.35 + 90*.20 + 85*.15 + 88*.15 + 80*.10 + 75*.05 = 85.95 → 85
    assert out["overall_score"] == 85
    assert out["final_decision"] == "推荐录用"


def test_hiring_decision_hard_veto_caps_score(monkeypatch):
    """核心维度（技能/问卷）任一低于 50 → 综合分封顶 79，最高只能待定"""
    _patch_llm(monkeypatch)
    cid = _mk_candidate()
    state = _state(cid,
                   skill_match_score=45, experience_score=90, education_score=90,
                   culture_match_score=90, questionnaire_score=90,
                   interview_scores=[{"round": 1, "score": 95}, {"round": 2, "score": 95}, {"round": 3, "score": 95}])
    out = asyncio.run(rg.generate_hiring_decision(state))
    # 加权综合分 82（95*.35+45*.20+90*.15+90*.15+90*.10+90*.05），硬否决封顶 79 → 待定
    assert out["overall_score"] == 79
    assert out["final_decision"] == "待定"


# ---------------------------------------------------------------- 终局
def test_finalize_hire_writes_status_and_pool(monkeypatch):
    cid = _mk_candidate()
    out = asyncio.run(rg.finalize_hire(_state(cid, overall_score=86, final_decision="推荐录用",
                                              interview_scores=[{"round": 1, "score": 90}])))
    assert out["current_step"] == "hired" and out["workflow_progress"] == 100
    with SessionLocal() as db:
        c = db.query(Candidate).filter(Candidate.id == cid).first()
        assert c.status == "hired"
        pool = db.query(TalentPool).filter(TalentPool.candidate_id == cid).first()
        assert "已录用" in pool.tags


def test_move_to_talent_pool(monkeypatch):
    cid = _mk_candidate()
    out = asyncio.run(rg.move_to_talent_pool(_state(cid, overall_score=70)))
    assert "待定" in out["final_decision"]
    with SessionLocal() as db:
        c = db.query(Candidate).filter(Candidate.id == cid).first()
        assert c.status == "talent_pool"


@pytest.mark.parametrize("step,expect_reason", [
    ("assess_cultural_fit", "文化契合度不达标"),
    ("questionnaire_scored", "问卷测评不达标"),
    ("interview_2_done", "第二轮面试不通过"),
    ("unknown_step", "流程淘汰"),
])
def test_reject_to_pool_reasons(monkeypatch, step, expect_reason):
    cid = _mk_candidate()
    out = asyncio.run(rg.reject_to_pool(_state(cid, current_step=step)))
    assert expect_reason in out["final_decision"]
    with SessionLocal() as db:
        c = db.query(Candidate).filter(Candidate.id == cid).first()
        assert c.status == "rejected"


def test_reject_to_pool_estimates_overall_before_decision(monkeypatch):
    """评审前淘汰（无 overall_score）时按已有分数估算综合分"""
    cid = _mk_candidate()
    out = asyncio.run(rg.reject_to_pool(_state(cid, current_step="assess_cultural_fit",
                                               skill_match_score=80, culture_match_score=40)))
    assert 0 < out["overall_score"] <= 100


# ---------------------------------------------------------------- 图组装
def test_build_recruitment_graph_compiles():
    graph = rg.build_recruitment_graph()
    # 编译后的图应包含全部核心节点
    assert "parse_resume" in graph.get_graph().nodes
    assert "generate_hiring_decision" in graph.get_graph().nodes
    assert "reject_to_pool" in graph.get_graph().nodes
