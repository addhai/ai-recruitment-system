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
    async def _fake(prompt, variables, default=None, *, call_site=None, **__):
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


def test_parse_resume_reparses_raw_text_snapshot(monkeypatch):
    """回归：上传接口会预置 parsed_data={"raw_text": ...} 原文快照。

    该快照非空，但不含结构化字段。若只用 `if not parsed` 判定"已解析"，
    就会跳过 LLM 结构化解析，导致 experience/education 永远为空，
    经验评估恒定 0 分、教育评估恒定低分。故必须重新解析。
    """
    cid = _mk_candidate()
    from src.workflow import db_actions as da
    da.save_resume_parsed(cid, {"raw_text": "候选人简历原文：5年Python后端，985硕士"})

    captured = {}

    async def _fake_llm(prompt, variables, default=None, *, call_site=None, **__):
        captured["called"] = True
        return {
            "basic_info": {"name": "张三"},
            "skills": ["Python", "FastAPI"],
            "experience": [{"company": "某科技公司", "role": "后端负责人"}],
            "education": [{"school": "某985高校", "degree": "硕士"}],
        }

    monkeypatch.setattr(rg, "_llm_json", _fake_llm)

    out = asyncio.run(rg.parse_resume(_state(cid)))

    assert captured.get("called") is True, "原文快照不应被当作已完成结构化解析"
    assert out["parsed_resume"]["experience"], "experience 必须被解析出来"
    assert out["parsed_resume"]["education"], "education 必须被解析出来"
    # 原文快照不能被解析结果覆盖丢失
    assert "raw_text" in out["parsed_resume"]

    # 落库结果同样要带上结构化字段，供下游节点读取
    saved = da.get_resume_parsed(cid)
    assert saved.get("experience")
    assert saved.get("education")


def test_parse_resume_reuses_real_structured_cache(monkeypatch):
    """已有真实结构化结果时仍应跳过 LLM（幂等不被破坏）"""
    called = {"n": 0}

    async def _fake_llm(*a, **kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr(rg, "_llm_json", _fake_llm)
    cid = _mk_candidate()
    from src.workflow import db_actions as da
    da.save_resume_parsed(cid, {"skills": ["Go"], "experience": [{"years": 3}]})

    out = asyncio.run(rg.parse_resume(_state(cid)))

    assert called["n"] == 0
    assert out["parsed_resume"]["skills"] == ["Go"]


def test_experience_falls_back_to_projects_for_campus_candidates(monkeypatch):
    """回归：应届生简历 experience 为空、项目在 projects，不能恒判 0 分。

    真实简历（校招岗）只有项目经历、没有工作经历，
    若只读 experience，这批候选人经验匹配会永远是 0 分。
    """
    captured = {}

    async def _fake_llm(prompt, variables, default=None, *, call_site=None, **__):
        captured.update(variables)
        return {"score": 86, "analysis": "项目与职位高度对口", "years_relevant": 0}

    monkeypatch.setattr(rg, "_llm_json", _fake_llm)
    cid = _mk_candidate()

    state = _state(cid, parsed_resume={
        "skills": ["Python"],
        "experience": [],                                   # 应届生：无工作经历
        "projects": [{"name": "智能客服Agent平台", "core_work": ["LangGraph 编排"]}],
    })
    out = asyncio.run(rg.evaluate_experience(state))

    assert "应届生" in captured["candidate_type"]
    assert "智能客服Agent平台" in captured["experience"], "必须回退到项目经历"
    assert out["experience_score"] == 86


def test_experience_prefers_work_history_when_present(monkeypatch):
    """有正式工作经历时仍以工作经历为准，不被项目覆盖"""
    captured = {}

    async def _fake_llm(prompt, variables, default=None, *, call_site=None, **__):
        captured.update(variables)
        return {"score": 78, "analysis": "同岗位 3 年", "years_relevant": 3}

    monkeypatch.setattr(rg, "_llm_json", _fake_llm)
    cid = _mk_candidate()

    state = _state(cid, parsed_resume={
        "experience": [{"company": "某科技公司", "role": "后端工程师", "years": 3}],
        "projects": [{"name": "业余项目"}],
    })
    asyncio.run(rg.evaluate_experience(state))

    assert "有正式工作经历" in captured["candidate_type"]
    assert "某科技公司" in captured["experience"]
    assert "业余项目" not in captured["experience"]


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
    """三段判定：>=60 通过 / [50,60) 标记待复核但不阻塞 / <50 淘汰"""
    from src.config import settings
    hi, lo = settings.CULTURE_PASS_THRESHOLD, settings.CULTURE_REVIEW_THRESHOLD
    assert rg.check_culture_pass({"culture_match_score": hi}) == "pass"
    assert rg.check_culture_pass({"culture_match_score": hi + 10}) == "pass"
    assert rg.check_culture_pass({"culture_match_score": lo}) == "review"
    assert rg.check_culture_pass({"culture_match_score": hi - 0.1}) == "review"
    assert rg.check_culture_pass({"culture_match_score": lo - 0.1}) == "reject"
    assert rg.check_culture_pass({"culture_match_score": 0}) == "reject"
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
def test_compute_overall_renormalizes_over_assessed_dims():
    """缺失维度按剩余权重重新归一化，不填默认值

    旧实现在淘汰分支用 `(questionnaire_score or 60)`：问卷没跑时白送 60 分
    （虚增 9 分），真考 37 分反而更低——"没考"比"考砸了"得分高。
    """
    state = {"skill_match_score": 80, "experience_score": 60,
             "education_score": 70, "culture_match_score": 50}
    # questionnaire / interview 均未测评，不应出现在维度清单里
    score, assessed = rg.compute_overall(state)
    assert "questionnaire_score" not in assessed and "interview_avg" not in assessed
    assert set(assessed) == {"skill_match_score", "experience_score",
                             "education_score", "culture_match_score"}
    # 80*.2 + 60*.15 + 70*.1 + 50*.05 = 34.5，除以剩余权重 0.5 → 69
    assert score == 69


def test_compute_overall_empty_state():
    assert rg.compute_overall({}) == (0, [])


def test_compute_overall_treats_zero_as_scored_not_missing():
    """0 分是「测评过且得 0 分」，必须参与计算而不是被当成未测评"""
    _score, assessed = rg.compute_overall({"skill_match_score": 0,
                                            "experience_score": 80})
    assert "skill_match_score" in assessed


def test_compute_overall_same_for_terminal_and_reject_paths(monkeypatch):
    """回归：终局与淘汰对同一份 state 必须得到同一个综合分（消除双公式）"""
    state = _state(0, skill_match_score=82, experience_score=86,
                   education_score=92, culture_match_score=58)
    shared = dict(state)
    shared["current_step"] = "assess_cultural_fit"
    cid = _mk_candidate()
    shared["candidate_id"] = cid
    overall_reject = asyncio.run(rg.reject_to_pool(shared))
    assert overall_reject["overall_score"] == rg.compute_overall(shared)[0]


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


def test_hard_veto_triggers_on_zero_score(monkeypatch):
    """回归：0 分是真实测评结果，必须触发硬否决。

    旧写法 `if x > 0` 会把 0 分当成"未测评"而放过：
    问卷 0 分 + 其余满分时旧逻辑算 83 分直接「推荐录用」，
    新逻辑封顶 79 只能「待定」。
    """
    _patch_llm(monkeypatch)
    cid = _mk_candidate()
    state = _state(cid,
                   skill_match_score=100, experience_score=100, education_score=100,
                   culture_match_score=100, questionnaire_score=0,
                   interview_scores=[{"round": 1, "score": 95}, {"round": 2, "score": 95}])
    out = asyncio.run(rg.generate_hiring_decision(state))
    assert out["overall_score"] == 79, "问卷 0 分应触发硬否决封顶"
    assert out["final_decision"] == "待定"


def test_hard_veto_not_triggered_when_dimension_unassessed(monkeypatch):
    """未测评(None) 不应触发硬否决——否则"没考"会被当成"考砸了"封顶"""
    _patch_llm(monkeypatch)
    cid = _mk_candidate()
    state = _state(cid,
                   skill_match_score=90, experience_score=90, education_score=90,
                   culture_match_score=90, questionnaire_score=None,
                   interview_scores=[{"round": 1, "score": 90}, {"round": 2, "score": 90}])
    out = asyncio.run(rg.generate_hiring_decision(state))
    assert out["overall_score"] > 79, "问卷未测评不应触发封顶"


def test_final_decision_locks_for_human_review(monkeypatch):
    """回归：needs_review 时锁档「待人工复核」，且不调用 LLM 决策

    LLM 允许"有突出亮点时上调一档"，若放行就会把边缘样本直接推成录用。
    """
    called = {"n": 0}

    async def _counting_llm(prompt, variables, default=None, *, call_site=None, **__):
        called["n"] += 1
        return {"decision": "推荐录用", "reasons": ["强行上调"], "risks": []}
    monkeypatch.setattr(rg, "_llm_json", _counting_llm)

    cid = _mk_candidate()
    state = _state(cid,
                   skill_match_score=95, experience_score=95, education_score=95,
                   culture_match_score=55, questionnaire_score=90,
                   interview_scores=[{"round": 1, "score": 95}],
                   needs_review=True,
                   review_detail={"dimension": "文化契合", "score": 55,
                                  "pass_threshold": 60, "review_threshold": 50,
                                  "degraded": False})
    out = asyncio.run(rg.generate_hiring_decision(state))
    assert out["final_decision"] == "待人工复核"
    assert called["n"] == 0, "人工复核态不应再调用 LLM 做决策"
    assert out["final_recommendation"]["review_detail"]["score"] == 55


def test_check_final_decision_routes_review_to_pool():
    assert rg.check_final_decision({"final_decision": "待人工复核"}) == "pool"
    assert rg.check_final_decision({"final_decision": "推荐录用"}) == "hire"
    assert rg.check_final_decision({"final_decision": "不推荐（问卷测评不达标）"}) == "reject"


def test_review_state_lands_in_pending_review(monkeypatch):
    """复核态终局落到 pending_review 状态与可辨识的人才池标签"""
    cid = _mk_candidate()
    state = _state(cid, overall_score=78, needs_review=True,
                   review_detail={"score": 55, "review_threshold": 50,
                                  "pass_threshold": 60, "degraded": False})
    out = asyncio.run(rg.move_to_talent_pool(state))
    assert out["final_decision"] == "待人工复核"
    assert out["current_step"] == "pending_review"
    with SessionLocal() as db:
        c = db.query(Candidate).filter(Candidate.id == cid).first()
        assert c.status == "pending_review"
        pool = db.query(TalentPool).filter(TalentPool.candidate_id == cid).first()
        assert "待人工复核" in pool.tags and "文化契合边缘" in pool.tags


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
