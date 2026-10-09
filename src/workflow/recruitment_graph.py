"""招聘全链路工作流（LangGraph 状态机 + Human-in-the-loop）。

与旧版"一次性模拟跑完"的本质区别：
1. 每个节点都有真实业务副作用：写 Candidate/Resume/Evaluation/Questionnaire/Interview/TalentPool 表；
2. 问卷、三轮面试共 4 个 interrupt 挂起点，等待真实人工事件（问卷作答 / 面试录入）后用 Command(resume=...) 恢复；
3. 淘汰与待定分支都会沉淀人才池，录用分支回写候选人状态；
4. LLM 不可用时所有评分节点自动降级为规则评分，流程仍可走通。

节点重放语义：resume 时节点函数会整体重跑，interrupt() 返回恢复值。
因此所有写库动作必须幂等（见 db_actions），外部通知仅在"首次创建"时发送。
"""
import asyncio
from typing import TypedDict, List, Dict, Any, Optional, Tuple

from langgraph.graph import StateGraph, END
from langgraph.types import interrupt

from src.config import settings
from src.workflow import db_actions as db
from src.sse import notification as sse
# 统一 LLM 调用入口（与 JD 解析共用同一套超时/重试/降级策略）。
# 以 _llm_json 之名导入，保留既有用法与测试里的 monkeypatch(rg, "_llm_json")。
from src.services.llm_json import llm_json as _llm_json, get_llm as _get_llm, extract_llm_error as _extract_llm_error  # noqa: F401


class RecruitmentState(TypedDict, total=False):
    candidate_id: int
    candidate_name: str
    resume_text: str
    position_requirements: str
    position: str
    job_description_id: Optional[int]
    jd_profile: Optional[Dict[str, Any]]
    jd_source: Optional[str]
    # 评分口径指纹：由 runner 在启动时算一次并随状态流转，
    # 这样同一次运行里"启动时记录的指纹"与"完成时记录的指纹"必然一致
    # （中途有人改了模型配置也不会让同一次运行出现两个指纹）。
    scoring_fingerprint: Optional[str]
    needs_review: Optional[bool]
    review_reason: Optional[str]
    review_detail: Optional[Dict[str, Any]]
    interview_r3_skipped: Optional[bool]
    culture_unconstrained: Optional[bool]
    unconstrained_dimensions: Optional[List[str]]
    parsed_resume: Optional[Dict[str, Any]]
    skill_match_score: Optional[float]
    skill_match_details: Optional[Dict[str, Any]]
    experience_score: Optional[float]
    education_score: Optional[float]
    culture_match_score: Optional[float]
    culture_match_details: Optional[Dict[str, Any]]
    communication_score: Optional[float]
    questionnaire_id: Optional[int]
    questionnaire_score: Optional[int]
    interview_round: Optional[int]
    interview_scores: Optional[List[Dict[str, Any]]]
    final_decision: Optional[str]
    final_recommendation: Optional[Dict[str, Any]]
    overall_score: Optional[int]
    reject_reason: Optional[str]
    workflow_progress: int
    current_step: str


# ================================================================ 阶段一：简历解析

# 结构化解析结果的特征键：上传环节会先写一份 {"raw_text": ...} 原文快照，
# 它只是保证工作流运行前简历页有内容，不算"已解析"
_STRUCTURAL_KEYS = ("basic_info", "skills", "skills_technical", "experience", "education")


def _is_structurally_parsed(parsed: Any) -> bool:
    """判断是否已有真正的结构化解析结果（而非仅原文快照/空壳）"""
    if not isinstance(parsed, dict) or not parsed:
        return False
    return any(k in parsed for k in _STRUCTURAL_KEYS)


async def parse_resume(state: RecruitmentState) -> RecruitmentState:
    cid = state["candidate_id"]
    # 幂等：只有真正结构化解析过才复用。
    # 不能用 `if not parsed`：上传接口已预置 {"raw_text": ...} 快照，
    # 非空即被当成"已解析"，导致 experience/education 永远拿不到，
    # 后续经验/教育评估恒定拿 0 分与低分。
    parsed = db.get_resume_parsed(cid)
    if not _is_structurally_parsed(parsed):
        raw_snapshot = parsed if isinstance(parsed, dict) else {}
        default_parsed = {
            "basic_info": {"name": state.get("candidate_name", "")},
            "skills": ["简历解析未完成(LLM不可用)"],
            "experience": [],
            "education": [],
        }
        parsed = await _llm_json(
            """解析以下简历内容，提取关键信息并结构化输出。

简历内容：
{resume_text}

请提取：基本信息、教育背景、工作经历、技能清单、项目经验、语言能力。
请以JSON格式输出，键名为：basic_info, education, experience, skills, projects, language""",
            {"resume_text": (state.get("resume_text") or "")[:6000]},
            default_parsed,

        call_site="parse_resume",
    )
        # 保留上传时留存的原文快照，避免解析结果覆盖后丢失简历正文
        parsed = {**raw_snapshot, **parsed}
        skills = parsed.get("skills") or parsed.get("skills_technical")
        db.save_resume_parsed(cid, parsed, skills=skills)
    return {**state, "parsed_resume": parsed, "workflow_progress": 5, "current_step": "parse_resume"}


async def extract_skills(state: RecruitmentState) -> RecruitmentState:
    parsed = state.get("parsed_resume") or {}
    skills_data = await _llm_json(
        """从以下解析后的简历中提取技能信息，按类别分类。

解析后的简历：
{parsed_resume}

输出JSON格式，包含 skills_technical, skills_soft, domain_knowledge 三个数组字段。""",
        {"parsed_resume": str(parsed)[:4000]},
        {"skills_technical": parsed.get("skills", []), "skills_soft": [], "domain_knowledge": []},

        call_site="extract_skills",
    )
    merged = {**parsed, **skills_data}
    db.save_resume_parsed(state["candidate_id"], merged, skills=skills_data.get("skills_technical"))
    return {**state, "parsed_resume": merged, "workflow_progress": 10, "current_step": "extract_skills"}


# ================================================================ 阶段二：人岗匹配评估

def _jd_brief(state: RecruitmentState) -> str:
    """把结构化 JD 渲染成给评审模型看的紧凑描述（无可用 JD 时回退岗位名）"""
    jd = state.get("jd_profile")
    if not isinstance(jd, dict) or not jd:
        return state.get("position_requirements") or state.get("position") or "通用岗位要求"
    return str(jd)[:3000]


def _jd_required_skills(state: RecruitmentState) -> List[str]:
    jd = state.get("jd_profile")
    if not isinstance(jd, dict):
        return []
    return [s.get("skill", "") for s in (jd.get("required_skills") or []) if s.get("skill")]


def _jd_list(state: RecruitmentState, key: str) -> List[str]:
    jd = state.get("jd_profile")
    if not isinstance(jd, dict):
        return []
    value = jd.get(key)
    return [str(v) for v in value] if isinstance(value, list) else []


def _jd_basic_str(state: RecruitmentState, key: str) -> str:
    """取 JD basic 下的标量字段；key 传顶层数组名（如 responsibilities）时取列表"""
    jd = state.get("jd_profile")
    if not isinstance(jd, dict):
        return ""
    if key == "responsibilities":
        return "; ".join(_jd_list(state, key))[:1500]
    basic = jd.get("basic")
    if isinstance(basic, dict):
        return str(basic.get(key) or "")
    return ""


def is_unconstrained(value: Any) -> bool:
    """判断 JD 的某项要求是否为「无限制」。

    显式填"不限制/无/不限"，以及 JD 原文根本没写——两种情况都视为不限制：
    JD 没写的要求不能凭空拿来卡候选人。
    """
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return True
        return text in settings.UNCONSTRAINED_MARKERS
    if isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value if str(v).strip()]
        # 列表非空但全是不限制标记（如 ["不限制"]），同样视为未设限
        return not items or all(v in settings.UNCONSTRAINED_MARKERS for v in items)
    return False


def _unconstrained_dims(state: RecruitmentState) -> List[str]:
    """列出本次因 JD 未设限而不参与评分的维度（用 state key，便于前端按字段处理）"""
    return list(state.get("unconstrained_dimensions") or [])


async def evaluate_skill_match(state: RecruitmentState) -> RecruitmentState:
    parsed = state.get("parsed_resume") or {}
    required = _jd_required_skills(state)
    result = await _llm_json(
        """你是严格的招聘技术评审。评估候选人「技能」与岗位硬性要求的匹配度。

岗位硬性要求技能（必须逐条核对）：{required_skills}
候选人技能清单：{skills}
技能使用佐证（工作经历与项目）：{evidence}
岗位完整画像：{jd_brief}

评分锚点（必须严格遵守）：
- 90-100：岗位硬性技能全部命中，且多数能在工作经历/项目中找到实际使用与成果佐证
- 75-89：硬性技能基本命中，1-2 项次要技能缺失，或部分技能缺少佐证
- 60-74：硬性技能命中一半左右，或技能仅出现在清单里、经历中无使用痕迹
- 40-59：多项硬性技能缺失
- 0-39：硬性技能基本不具备

硬性规则：
1. 只对着"岗位硬性要求技能"逐条核对；命中与否只看候选人简历中明确出现的内容，
   不得推测、脑补候选人可能具备的技能。
2. 命中但无使用佐证的技能，按锚点降档处理，不得仅凭清单给 90+。
3. missing_skills 必须列出岗位硬性要求中候选人未命中的项，供 HR 复核。
4. 评分必须拉开差距，证据不足时宁可给低分，禁止全员 70-85 的趋中打分。

输出JSON：{{"required_core_skills": [...], "matched_skills": [...], "missing_skills": [...], "score": number, "reason": "一句话说明扣分点"}}""",
        {
            "required_skills": str(required) if required else "（岗位画像未提供硬性技能，按通用技术岗评估）",
            "skills": str(parsed.get("skills_technical") or parsed.get("skills", [])),
            "evidence": str({"experience": parsed.get("experience", []),
                             "projects": parsed.get("projects", [])})[:3500],
            "jd_brief": _jd_brief(state),
        },
        {"matched_skills": [], "missing_skills": [], "score": 70},

        call_site="evaluate_skill_match",
    )
    score = float(result.get("score", 70))
    missing = result.get("missing_skills", []) or []
    db.upsert_evaluation(state["candidate_id"], "技能匹配", score,
                         f"匹配: {','.join(result.get('matched_skills', [])[:6])}"
                         f"{'；缺失: ' + ','.join(missing[:6]) if missing else ''}"
                         f"；{result.get('reason', '')}")
    return {**state, "skill_match_score": score, "skill_match_details": result,
            "workflow_progress": 18, "current_step": "evaluate_skill_match"}


async def evaluate_experience(state: RecruitmentState) -> RecruitmentState:
    parsed = state.get("parsed_resume") or {}
    years_req = _jd_basic_str(state, "experience_years_required")
    years_unconstrained = is_unconstrained(years_req)

    # 应届生/在校生简历里 experience 为空是正常的，两个完整项目都落在 projects。
    # 只读 experience 会对这批候选人恒定判 0 分（校招、社招应届岗是主要场景），
    # 因此无工作经历时回退到项目经历，并明确告诉模型这是"在校项目经验"。
    work = parsed.get("experience") or []
    projects = parsed.get("projects") or []
    if work:
        experience_input = str(work)[:3000]
        candidate_type = "有正式工作经历"
    elif projects:
        experience_input = str(projects)[:3000]
        candidate_type = "应届生/在校生，无正式工作经历，以下为简历中的项目经历"
    else:
        experience_input = "[]"
        candidate_type = "简历未提供工作经历与项目经历"

    # 岗位对年限不设限 → 不按年限扣分，只评职责相关度
    if years_unconstrained:
        years_text = "岗位对工作年限不设限制，不按年限扣分，只评估与岗位职责的相关性"
    else:
        years_text = years_req

    result = await _llm_json(
        """你是严格的招聘评审。评估候选人「经验」与职位的匹配度。

候选人类型：{candidate_type}
候选人经历：{experience}
岗位经验要求：{jd_experience_req}
岗位职责：{jd_responsibilities}
岗位完整画像：{jd_brief}

评分锚点（必须严格遵守）：
- 90-100：达到岗位年限要求，且有与岗位职责高度相关的主导项目并成果可量化
- 75-89：年限基本达标，职责相关度高
- 60-74：年限达标但职责相关度一般，或成果描述空泛
- 40-59：年限明显不足，或经历主要与岗位职责无关
- 0-39：无任何相关经历

应届生/在校生适用锚点（无工作年限时按项目深度评估，不得因缺年限直接判 0）：
- 85-95：项目与岗位职责高度对口，技术选型讲得清取舍，有量化成果（性能/准确率/成本）
- 70-84：项目与岗位职责基本对口，能独立完成完整交付，成果部分量化
- 55-69：项目与岗位职责沾边，或成果描述空泛、无量化证据
- 0-54：项目与岗位职责无关，或简历未提供任何有效项目描述

硬性规则：
1. 只依据简历中明确写出的公司、岗位、年限、项目成果打分，不得脑补。
2. 与"岗位经验要求"逐条比对，年限不足、职责不相关、无量化证据都必须显著扣分。
3. 禁止趋中打分，强弱候选人必须拉开分差。

输出JSON：{{"years_relevant": number, "analysis": "依据简历事实的分析，引用具体经历", "score": number}}""",
        {
            "candidate_type": candidate_type,
            "experience": experience_input,
            "jd_experience_req": years_text,
            "jd_responsibilities": _jd_basic_str(state, "responsibilities") or "（见岗位完整画像）",
            "jd_brief": _jd_brief(state),
        },
        {"analysis": "经验评估降级", "score": 70},

        call_site="evaluate_experience",
    )
    score = float(result.get("score", 70))
    db.upsert_evaluation(state["candidate_id"], "经验匹配", score, result.get("analysis", ""))
    return {**state, "experience_score": score,
            "skill_match_details": {**(state.get("skill_match_details") or {}), "experience_analysis": result},
            "workflow_progress": 26, "current_step": "evaluate_experience"}


async def evaluate_education(state: RecruitmentState) -> RecruitmentState:
    # JD 明确不设学历限制（填"不限制"或原文没写）→ 该维度不评分，
    # 也不参与综合分，更不会因为学历不达标而扣分。
    if is_unconstrained(_jd_basic_str(state, "education_required")):
        out = {**state,
                "unconstrained_dimensions": _unconstrained_dims(state) + ["education_score"],
                "workflow_progress": 33, "current_step": "evaluate_education"}
        return out

    parsed = state.get("parsed_resume") or {}
    result = await _llm_json(
        """你是严格的招聘评审。评估候选人「教育背景」与职位的匹配度。

候选人教育背景：{education}
岗位学历要求：{jd_edu_req}
岗位学历加分项：{jd_edu_pref}
岗位完整画像：{jd_brief}

评分锚点（必须严格遵守）：
- 90-100：达到岗位学历要求，且专业与岗位职责高度对口
- 75-89：达到岗位学历要求，专业相近或有相关辅修/认证
- 60-74：低于岗位学历要求但专业高度对口，或学历达标但专业不相关
- 40-59：明显低于岗位学历要求且专业不相关
- 0-39：简历未提供教育信息

硬性规则：
1. 只依据简历明确写出的学历层次、院校、专业打分。
2. 岗位未写明学历要求时，按"通用研发岗至少本科"评估，并在分析中说明这一点。
3. 简历未提供教育信息时给 40 分以下，不得默认本科。
4. 禁止趋中打分。

输出JSON：{{"analysis": "引用简历中的具体学历/专业事实", "score": number}}""",
        {
            "education": str(parsed.get("education", []))[:2000],
            "jd_edu_req": _jd_basic_str(state, "education_required"),
            "jd_edu_pref": _jd_basic_str(state, "education_preferred") or "无",
            "jd_brief": _jd_brief(state),
        },
        {"analysis": "教育评估降级", "score": 70},

        call_site="evaluate_education",
    )
    score = float(result.get("score", 70))
    db.upsert_evaluation(state["candidate_id"], "教育匹配", score, result.get("analysis", ""))
    return {**state, "education_score": score,
            "skill_match_details": {**(state.get("skill_match_details") or {}), "education_analysis": result},
            "workflow_progress": 33, "current_step": "evaluate_education"}


async def assess_cultural_fit(state: RecruitmentState) -> RecruitmentState:
    # 价值观必须来自该岗位的 JD 原文。此前这里用的是代码里写死的
    # "创新精神、团队协作、客户导向…"，与具体岗位无关，导致同一份简历
    # 多次运行给出 58/72 两种结果——这是评分不可复现的主要来源。
    culture_values = _jd_list(state, "culture_values")

    # 岗位未设价值观（显式填"不限制"或原文没写）→ 该维度不评分、不参与综合分，
    # 文化门槛直接放行。拿一份没写价值观的 JD 去要求候选人"体现企业文化"，
    # 等于凭空造出候选人不满足的要求。
    if is_unconstrained(culture_values):
        return {**state,
                "culture_match_score": None,
                "culture_unconstrained": True,
                "unconstrained_dimensions": _unconstrained_dims(state) + ["culture_match_score"],
                "needs_review": False,
                "review_reason": None,
                "review_detail": None,
                "workflow_progress": 40, "current_step": "assess_cultural_fit"}

    values_text = "、".join(culture_values)

    result = await _llm_json(
        """你是严格的招聘评审。基于简历中「可见的客观证据」评估候选人与该岗位价值观的契合度。

本岗位 JD 中明确写出的价值观/我们看重的能力：{culture_values}
候选人简历信息：{parsed_resume}

可作为证据的客观事实：跨部门/团队协作项目、开源或技术分享、长期稳定任职、
客户对接或乙方经历、持续学习痕迹（认证/自学/跨领域）、主导并交付的责任成果。
简历中没有的内容一律视为证据缺失，不得凭空推测候选人"应该具备"某素质。

评分锚点（必须严格遵守）：
- 85-100：简历中能找到 3 项以上本岗位价值观的明确事实证据
- 70-84：能找到 2 项明确证据
- 55-69：仅 1 项证据或证据间接
- 40-54：简历未提供任何有效文化证据（信息不足的默认档）
- 0-39：简历中有负面信号（如频繁跳槽且无合理解释）

输出JSON：{{"evidence": ["逐条列出简历中的具体事实，注明对应本岗位哪项价值观"], "score": number, "reasons": ["扣分理由，明确指出哪些价值观无证据"]}}""",
        {
            "culture_values": values_text,
            "parsed_resume": str(state.get("parsed_resume") or {})[:3000],
        },
        {"evidence": [], "score": 70, "reasons": ["文化评估降级，默认通过"]},

        call_site="assess_cultural_fit",
    )
    score = float(result.get("score", 70))
    db.upsert_evaluation(state["candidate_id"], "文化契合", score,
                         "；".join(result.get("reasons", []))[:300])

    # 三段判定所需标记（幂等覆盖写：节点重放时结果一致）
    degraded = not result.get("reasons") and not result.get("evidence")
    in_review_band = (settings.CULTURE_REVIEW_THRESHOLD <= score < settings.CULTURE_PASS_THRESHOLD)
    review_detail = {
        "dimension": "文化契合",
        "score": int(score),
        "pass_threshold": settings.CULTURE_PASS_THRESHOLD,
        "review_threshold": settings.CULTURE_REVIEW_THRESHOLD,
        "culture_values": culture_values,
        # 降级默认分（LLM 挂了）也 >= 复核下限，会直接放行，必须让 HR 知道这是兜底值
        "degraded": degraded,
    }
    return {**state, "culture_match_score": score, "culture_match_details": result,
            "culture_unconstrained": False,
            "unconstrained_dimensions": _unconstrained_dims(state),
            "needs_review": in_review_band,
            "review_reason": "文化契合处于待复核区间" if in_review_band else None,
            "review_detail": review_detail if in_review_band else None,
            "workflow_progress": 40, "current_step": "assess_cultural_fit"}


def check_culture_pass(state: RecruitmentState) -> str:
    """三段判定：>=60 通过 / [50,60) 标记待复核但继续跑 / <50 淘汰。

    此前只有 60 一档一票否决，而文化在综合分里仅占 5%——实测同一份简历
    在 58/72 之间横跳，结论直接翻转。中间区间改为打标记交给人工裁决。
    """
    score = state.get("culture_match_score")
    if score is None:
        # 岗位未设价值观 → 不评估也不卡人，直接放行；
        # 真正"没测到"才判 reject（保守处理）
        return "pass" if state.get("culture_unconstrained") else "reject"
    if score >= settings.CULTURE_PASS_THRESHOLD:
        return "pass"
    if score >= settings.CULTURE_REVIEW_THRESHOLD:
        return "review"      # 不阻塞：与 pass 走同一条边，流程继续跑完
    return "reject"


# ================================================================ 阶段三：自动问询筛选（挂起点 1）

async def questionnaire_stage(state: RecruitmentState) -> RecruitmentState:
    """生成问卷并写库 → interrupt 挂起等待作答 → 恢复后对真实答案评分"""
    cid = state["candidate_id"]
    db.set_candidate_status(cid, "questionnaire")

    q_name = f"AI测评卷-{cid}-{state.get('candidate_name', '候选人')}"
    required_skills = _jd_required_skills(state)
    gen = await _llm_json(
        """根据岗位硬性要求和候选人简历生成测评问卷。

岗位硬性要求技能：{required_skills}
岗位职责：{jd_responsibilities}
岗位完整画像：{jd_brief}
候选人简历摘要：{resume_summary}

生成5个测评问题，题目必须围绕该岗位的实际要求，覆盖核心技术栈、架构设计、问题解决能力。
输出JSON：{{"questions": [{{"question": "...", "type": "问答题"}}]}}""",
        {
            "required_skills": str(required_skills) if required_skills else "（岗位画像未提供硬性技能）",
            "jd_responsibilities": _jd_basic_str(state, "responsibilities") or "（见岗位完整画像）",
            "jd_brief": _jd_brief(state),
            "resume_summary": str(state.get("parsed_resume") or {})[:2000],
        },
        {"questions": [
            {"question": "请描述你最具代表性的项目及你在其中的职责", "type": "问答题"},
            {"question": "你如何处理线上紧急故障？请举例说明", "type": "问答题"},
            {"question": "你最擅长的技术栈是什么？如何保持技术更新？", "type": "问答题"},
        ]},

        call_site="questionnaire_stage",
    )
    questions = gen.get("questions", [])
    for i, q in enumerate(questions, 1):
        q.setdefault("id", i)

    created = db.create_questionnaire(cid, q_name, "ai_auto", questions)
    questionnaire_id = created["questionnaire_id"]
    if created["created"]:
        # 通知仅在首次建卷时发送（resume 重放时 created=False，跳过避免重复通知）
        await sse.notify_questionnaire_generated(cid, questionnaire_id, "ai_auto")

    # ---- 挂起：等待候选人/HR 提交问卷答案 ----
    answer_payload = interrupt({
        "type": "await_questionnaire",
        "candidate_id": cid,
        "questionnaire_id": questionnaire_id,
        "question_count": len(questions),
    })

    # ---- 恢复：对真实答案评分 ----
    responses = answer_payload.get("responses") or answer_payload.get("answers") or {}
    answered = sum(1 for v in (responses.values() if isinstance(responses, dict) else responses)
                   if v and str(v).strip())
    total = max(len(questions), 1)

    scoring = await _llm_json(
        """你是严格的招聘测评官，根据岗位要求对候选人问卷答案评分。

岗位完整画像：{jd_brief}
问卷题目：{questions}
候选人答案：{responses}

评分锚点（必须严格遵守）：
- 90-100：答案切题、有具体项目/数据/方法论支撑，展现深度
- 75-89：答案切题但深度一般，有案例但不够具体
- 60-74：答案笼统、泛泛而谈，仅表态无实质内容
- 40-59：多数答案敷衍、过短或明显跑题
- 0-39：大面积未作答或答案与问题无关

硬性规则：
1. 逐题评分后汇总，不得只凭整体印象给分。
2. 答案长度过短（少于一句话）或空泛口号（如"我很努力"）必须压分。
3. 禁止趋中打分，答案质量高低必须体现在分差上。

输出JSON：{{"per_question": [{{"q": "题号", "score": number, "comment": "一句话"}}], "score": number, "feedback": "总体评语，指出最强和最弱的答案"}}""",
        {
            "jd_brief": _jd_brief(state),
            "questions": str(questions)[:2000],
            "responses": str(responses)[:4000],
        },
        # 降级：按作答完整度给 60-90 分
        {"score": int(60 + 30 * answered / total), "feedback": "规则评分：按作答完整度估算"},
        call_site="score_questionnaire",
    )
    score = int(max(0, min(100, scoring.get("score", 60))))

    db.save_questionnaire_response(cid, questionnaire_id, responses if isinstance(responses, dict) else {"answers": responses}, score)
    db.upsert_evaluation(cid, "问卷测评", score, scoring.get("feedback", "")[:300])
    await sse.notify_questionnaire_submitted(cid, questionnaire_id, score)

    return {**state, "questionnaire_id": questionnaire_id, "questionnaire_score": score,
            "workflow_progress": 52, "current_step": "questionnaire_scored"}


def check_questionnaire_pass(state: RecruitmentState) -> str:
    if state.get("questionnaire_score", 0) >= 60:
        return "pass"
    return "reject"


# ================================================================ 阶段四：面试智能排期 + 人工录入（挂起点 2/3/4）

# 各轮次面试侧重（与人事制度「招聘流程规范」对齐：技术 → 综合 → HR）
_INTERVIEW_FOCUS = {
    1: "第一轮技术面试：重点考察硬技能深度、项目真实性与技术方案能力。问题应围绕简历中的具体技能和项目经历深挖，要求候选人讲清技术细节、取舍依据，避免空泛。",
    2: "第二轮综合面试：以STAR行为面试题为主（情境Situation-任务Task-行动Action-结果Result），重点考察跨团队协作、冲突处理、抗压能力、推动落地的真实案例。",
    3: "第三轮HR面试：重点考察求职动机、职业规划、稳定性、文化价值观契合度、薪资与到岗预期，判断长期合作可能性。",
}

# LLM 不可用时的分轮次兜底题库
_FALLBACK_QUESTIONS = {
    1: [
        {"question": "请挑一个你简历中最有代表性的项目，讲讲你在其中承担的具体技术工作和关键决策。", "focus": "项目真实性与个人贡献边界"},
        {"question": "这个项目中遇到过的最大技术难题是什么？你是如何定位和解决的？", "focus": "问题定位与解决思路"},
        {"question": "针对岗位要求的核心技能，你给自己打几分？哪个部分最薄弱，准备怎么补？", "focus": "技能深度与自我认知"},
        {"question": "如果让你重新设计这个项目，你会在架构或方案上做哪些不同的选择？", "focus": "复盘能力与技术视野"},
        {"question": "讲一次你和他人对技术方案有分歧的经历，最后是怎么达成一致的？", "focus": "技术沟通与协作"},
    ],
    2: [
        {"question": "请讲一次你在资源或时间严重不足的情况下，仍然推动项目交付的经历。你具体做了什么？", "focus": "抗压与结果导向（STAR）"},
        {"question": "描述一次你和协作部门（研发/设计/运营等）发生严重分歧的情况，你是怎么处理的？", "focus": "跨团队冲突处理"},
        {"question": "讲一个你主动发起、超出岗位职责的改进或项目，结果如何？", "focus": "主动性与影响力"},
        {"question": "说一次你收到严厉负面反馈的经历，你当时的反应和后续改变是什么？", "focus": "自省与成长型思维"},
        {"question": "回顾过去两年，你认为自己最能体现成长的一件事是什么？为什么？", "focus": "成长轨迹与反思深度"},
    ],
    3: [
        {"question": "你为什么考虑离开上一家公司？选择机会时你最看重哪三个因素？", "focus": "求职动机真实性"},
        {"question": "未来3年你对自己的职业发展有什么规划？你希望在我们这里获得什么？", "focus": "职业规划与岗位匹配度"},
        {"question": "什么样的工作环境或管理方式会让你状态最好？什么情况会让你想离开？", "focus": "文化契合与稳定性"},
        {"question": "你目前的薪资结构和期望薪资是多少？到岗时间需要多久？", "focus": "薪资预期与到岗可行性"},
        {"question": "你手头还有其他offer或面试进程吗？如果多家同时给offer你怎么选？", "focus": "竞争态势与决策标准"},
    ],
}

_INTERVIEW_QUESTION_PROMPT = """你是资深招聘面试官，请为候选人出{round_no}轮面试题（共5题）。

轮次定位：
{round_focus}

目标岗位：{position}

候选人简历结构化信息：
{parsed_resume}

硬性要求：
1. 题目必须紧扣该候选人的真实经历和岗位要求，禁止出与简历无关的通用题；
2. 技术轮要针对简历提到的具体技能/项目深挖细节，行为轮每题都要能引出完整STAR讲述；
3. 每题同时给出"考察要点"，告诉面试官该听什么信号、什么回答算好/差；
4. 只输出JSON，格式：{{"questions": [{{"question": "题目", "focus": "考察要点与评分参考"}}]}}，恰好5题。"""


async def generate_interview_questions(state: RecruitmentState, round_no: int) -> List[Dict[str, str]]:
    """生成第 N 轮面试题：LLM 按轮次侧重 + 简历定制出题，失败降级为分轮次通用题库"""
    parsed = state.get("parsed_resume") or {}
    import json as _json
    default = _FALLBACK_QUESTIONS.get(round_no, _FALLBACK_QUESTIONS[1])
    result = await _llm_json(
        _INTERVIEW_QUESTION_PROMPT,
        {
            "round_no": round_no,
            "round_focus": _INTERVIEW_FOCUS.get(round_no, ""),
            "position": state.get("position") or state.get("position_requirements") or "",
            "parsed_resume": _json.dumps(parsed, ensure_ascii=False)[:3000],
        },
        default={"questions": default},

        call_site="generate_interview_questions",
    )
    questions = result.get("questions") if isinstance(result, dict) else None
    if not isinstance(questions, list) or not questions:
        return default
    # 过滤 LLM 返回的畸形项，缺字段用兜底题补齐到 5 题
    cleaned = [
        {"question": str(q.get("question", "")).strip(), "focus": str(q.get("focus", "")).strip()}
        for q in questions if isinstance(q, dict) and q.get("question")
    ]
    cleaned = [q for q in cleaned if q["question"]]
    if len(cleaned) < 5:
        cleaned.extend(default[len(cleaned):5])
    return cleaned[:5]


def make_interview_node(round_no: int, progress_schedule: int, progress_done: int):
    """工厂生成第 N 轮面试节点：自动排期写库 → interrupt 等待面试完成 → 回填真实分数"""

    async def interview_node(state: RecruitmentState) -> RecruitmentState:
        cid = state["candidate_id"]
        db.set_candidate_status(cid, "interviewing")

        # 幂等排期：重放/重入时复用已有面试记录
        info = db.schedule_interview(cid, round_no, state.get("position"))
        interview_id = info["interview_id"]

        # 若该轮面试已在库中标记完成（极端重放场景），直接读回分数
        existing = db.get_interview(cid, round_no)
        if existing and existing["status"] == "completed" and existing["score"] is not None:
            score = int(existing["score"])
        else:
            # 面试题：首次排期时 LLM 按轮次侧重生成；重放时从库读回，保证幂等
            questions = (existing or {}).get("questions")
            if info.get("created") or not questions:
                questions = await generate_interview_questions(state, round_no)
                db.update_interview_questions(interview_id, questions)
            if info.get("created"):
                # 首次排期才发送通知，避免 resume 重放重复推送
                scheduled_str = info.get("scheduled_at") or "待定"
                await sse.notify_interview_scheduled(cid, interview_id, round_no, scheduled_str)

            # ---- 挂起：等待 HR 录入真实面试结果 ----
            result = interrupt({
                "type": "await_interview",
                "candidate_id": cid,
                "round": round_no,
                "interview_id": interview_id,
                "scheduled_at": info.get("scheduled_at"),
                "questions": questions,
            })

            score = int(result.get("score", 0))
            feedback = result.get("feedback", "")
            notes = result.get("notes")
            db.complete_interview_record(interview_id, score, feedback, notes)
            await sse.notify_interview_completed(cid, interview_id, score, feedback)

        db.upsert_evaluation(cid, f"第{round_no}轮面试", score,
                             f"第{round_no}轮面试综合评分（人工录入）")

        scores = [s for s in (state.get("interview_scores") or []) if s.get("round") != round_no]
        scores.append({"round": round_no, "score": score})
        scores.sort(key=lambda s: s["round"])

        return {**state, "interview_round": round_no, "interview_scores": scores,
                "workflow_progress": progress_done, "current_step": f"interview_{round_no}_done"}

    interview_node.__name__ = f"interview_round_{round_no}"
    return interview_node


def make_interview_check(round_no: int):
    def check(state: RecruitmentState) -> str:
        for s in state.get("interview_scores") or []:
            if s.get("round") == round_no:
                return "pass" if s.get("score", 0) >= 70 else "reject"
        return "reject"
    return check


def make_interview_check_with_r3_skip(round_no: int):
    """第 2 轮的路由：在复核态且开关打开时跳过第 3 轮面试。

    跳过只是不花这一轮的成本，不代表结论变宽松——终局仍然锁档
    "待人工复核"，综合分里面试权重也不变（面试均分由前两轮构成）。
    """
    base_check = make_interview_check(round_no)

    def check(state: RecruitmentState) -> str:
        result = base_check(state)
        if result != "pass":
            return result
        if settings.SKIP_ROUND3_ON_REVIEW and state.get("needs_review"):
            return "skip_r3"
        return "pass"
    return check


# ================================================================ 阶段五：综合评审与决策

# 综合分权重：面试最强信号，文化从简历推断最不靠谱故权重最低。
# 终局与淘汰共用这一份定义——此前两处各写一套公式（且淘汰版漏掉经验与教育、
# 还用 `or 60` 给未测评的问卷白送 60 分），导致同一候选人走不同终局的综合分不可比。
OVERALL_WEIGHTS = {
    "interview_avg": 0.35,
    "skill_match_score": 0.20,
    "experience_score": 0.15,
    "questionnaire_score": 0.15,
    "education_score": 0.10,
    "culture_match_score": 0.05,
}

_SCORE_KEYS = ("skill_match_score", "experience_score", "questionnaire_score",
               "education_score", "culture_match_score")


def compute_overall(state: RecruitmentState) -> Tuple[int, List[str]]:
    """计算综合分，返回 (分数, 实际参与计算的维度列表)。

    缺失维度按剩余权重重新归一化，而不是填默认值：
    填默认值会让「没测评」凭空得分（问卷未跑按 60 计，真考了 37 分反而更低），
    也会让不同阶段的综合分互不可比。返回维度清单供前端提示
    「本分数基于 N 个维度」，避免把部分维度的分数误当成完整结论。
    """
    raw: Dict[str, float] = {}
    scores = state.get("interview_scores") or []
    if scores:
        raw["interview_avg"] = sum(s["score"] for s in scores) / len(scores)
    for key in _SCORE_KEYS:
        v = state.get(key)
        if v is not None:          # 显式 None 判定：区分「未测评」与「0 分」
            raw[key] = float(v)
    if not raw:
        return 0, []
    total_w = sum(OVERALL_WEIGHTS[k] for k in raw)
    value = sum(raw[k] * OVERALL_WEIGHTS[k] for k in raw) / total_w
    return int(value), sorted(raw.keys())

def _dim_text(value: Any, key: str, free_dims: List[str]) -> str:
    """维度传给决策模型的展示值。

    岗位未设限的维度绝不能以 0 传下去——决策模型会把它当成"考了 0 分"，
    从而写出"教育匹配为0分，需确认学历硬性要求"这类并不存在的风险。
    """
    if key in free_dims:
        return "岗位未设限，不参与评分"
    if value is None:
        return "未评估"
    return str(int(value))


async def generate_hiring_decision(state: RecruitmentState) -> RecruitmentState:
    cid = state["candidate_id"]
    interview_scores = state.get("interview_scores") or []
    interview_avg = (sum(s["score"] for s in interview_scores) / len(interview_scores)) if interview_scores else 0

    # 综合分由规则加权确定性计算（LLM 做加权算术不可靠，会与子项自相矛盾）。
    # 权重设计：面试最强信号，文化从简历推断最不靠谱故权重最低。
    skill = state.get("skill_match_score")
    experience = state.get("experience_score")
    education = state.get("education_score")
    culture = state.get("culture_match_score")
    questionnaire = state.get("questionnaire_score")

    overall, assessed = compute_overall(state)
    free_dims = _unconstrained_dims(state)
    # 硬性否决：核心维度「已测评且」低于 50，综合分封顶 79（最高只能"待定"，
    # 落入人才池；封顶后仍低于 65 则维持"不推荐"）
    # 用 is not None 判定：未测评(None) 不该触发否决，0 分则确实该触发。
    # 旧写法 `if x > 0` 把 0 分与未测评混为一谈，会漏掉真正考砸的核心维度。
    hard_veto = any(
        v is not None and v < 50 for v in (skill, questionnaire)
    )
    capped = min(overall, 79) if hard_veto else overall
    if capped >= 80:
        rule_decision = "推荐录用"
    elif capped >= 65:
        rule_decision = "待定"
    else:
        rule_decision = "不推荐"

    # 人工复核锁档：文化契合落在待复核区间时，禁止 LLM 上调档位直接录用，
    # 也不允许直接淘汰——统一锁到"待人工复核"，由 HR 事后批量裁决。
    if state.get("needs_review"):
        detail = state.get("review_detail") or {}
        db.upsert_evaluation(
            cid, "综合评审", capped,
            f"文化契合 {detail.get('score', '?')} 分处于待复核区间 "
            f"[{detail.get('review_threshold')}–{detail.get('pass_threshold')})，"
            f"决策权移交人工复核"
            + ("；该分数为 LLM 不可用时的兜底值" if detail.get("degraded") else "")
        )
        return {**state,
                "final_decision": "待人工复核",
                "interview_r3_skipped": bool(settings.SKIP_ROUND3_ON_REVIEW),
                "final_recommendation": {
                    "decision": "待人工复核",
                    "overall_score": capped,
                    "assessed_dimensions": assessed,
                    "interview_r3_skipped": bool(settings.SKIP_ROUND3_ON_REVIEW),
                    "reasons": [
                        f"文化契合 {detail.get('score', '?')} 分处于待复核区间，"
                        f"系统不自动做录用/淘汰决定，移交 HR 裁决"
                        + ("；已按配置跳过第三轮面试"
                           if settings.SKIP_ROUND3_ON_REVIEW else "")
                    ],
                    "risks": [],
                    "review_detail": detail,
                },
                "overall_score": capped,
                "workflow_progress": 92,
                "current_step": "generate_hiring_decision"}

    decision = await _llm_json(
        """你是招聘委员会负责人，基于各维度评分做出最终录用决策。

候选人：{candidate_name}
各维度分数：技能匹配 {skill}、经验匹配 {experience}、教育匹配 {education}、
文化契合 {culture}、问卷测评 {questionnaire}、面试均分 {interview_avg}（各轮明细：{interviews}）
系统按权重算出的综合分：{overall}；规则档位建议：{rule_decision}。

决策要求：
1. 综合分以系统计算的 {overall} 为准，你不要自行改分。
2. 标注"岗位未设限"的维度表示该岗位没有这项要求，**不是**考了 0 分，
   不得据此扣分、也**不得**把它写成风险项或合规隐患。
3. 决策档位原则上与规则档位一致；仅当存在明确硬伤（如核心维度低于 50、面试反馈差）
   时可下调一档，或有突出亮点（多项 90+）时上调一档，并必须在理由中说明依据。
4. 理由要引用具体维度分数和事实，禁止空话。

输出JSON：{{"decision": "推荐录用|待定|不推荐", "reasons": ["3条以内，引用具体分数和事实"], "risks": ["录用后需关注的风险，无则空数组"]}}""",
        {
            "candidate_name": state.get("candidate_name", ""),
            "skill": _dim_text(skill, "skill_match_score", free_dims),
            "experience": _dim_text(experience, "experience_score", free_dims),
            "education": _dim_text(education, "education_score", free_dims),
            "culture": _dim_text(culture, "culture_match_score", free_dims),
            "questionnaire": _dim_text(questionnaire, "questionnaire_score", free_dims),
            "interview_avg": round(interview_avg, 1),
            "interviews": str(interview_scores),
            "overall": capped, "rule_decision": rule_decision,
        },
        {"decision": rule_decision, "reasons": [f"规则综合评分 {capped} 分，档位 {rule_decision}"], "risks": []},

        call_site="generate_hiring_decision",
    )

    final_decision = decision.get("decision") or rule_decision
    decision["overall_score"] = capped  # 综合分始终以规则计算为准
    decision["assessed_dimensions"] = assessed  # 让前端能提示"本分数基于 N 个维度"
    db.upsert_evaluation(cid, "综合评审", capped, "；".join(decision.get("reasons", []))[:300])

    return {**state, "final_decision": final_decision,
            "final_recommendation": decision, "overall_score": capped,
            "workflow_progress": 92, "current_step": "generate_hiring_decision"}


def check_final_decision(state: RecruitmentState) -> str:
    decision = state.get("final_decision") or ""
    # 人工复核态既不录用也不淘汰，统一进人才池等待 HR 事后裁决
    if "待人工复核" in decision:
        return "pool"
    if "不推荐" in decision or "拒绝" in decision:
        return "reject"
    if "录用" in decision:
        return "hire"
    return "pool"


# ================================================================ 阶段六：终局（录用 / 人才池沉淀 / 淘汰）

async def _finish(cid: int, status: str, tags: List[str], notes: str,
                  decision: str, overall: int, state: RecruitmentState, progress_step: str) -> RecruitmentState:
    """终局公共动作：回写候选人状态、沉淀人才池、推送决策通知"""
    db.set_candidate_status(cid, status)
    db.upsert_talent_pool(cid, tags, notes)
    await sse.notify_hiring_decision(cid, decision, overall)
    return {**state, "workflow_progress": 100, "current_step": progress_step}


async def finalize_hire(state: RecruitmentState) -> RecruitmentState:
    cid = state["candidate_id"]
    overall = state.get("overall_score", 0)
    notes = (
        f"录用决策：{state.get('final_decision')}，综合评分 {overall}。"
        f"技能 {state.get('skill_match_score')}，文化 {state.get('culture_match_score')}，"
        f"问卷 {state.get('questionnaire_score')}，面试 {state.get('interview_scores')}"
    )
    return await _finish(cid, "hired", ["已录用", "全流程通过"], notes,
                         state.get("final_decision", "推荐录用"), overall, state, "hired")


async def move_to_talent_pool(state: RecruitmentState) -> RecruitmentState:
    cid = state["candidate_id"]
    overall = state.get("overall_score", 0)
    # 人工复核态：状态用独立的 pending_review，人才池打上可辨识的标签，
    # 便于 HR 在复核队列里一眼区分"待复核"与普通"待定"
    if state.get("needs_review"):
        detail = state.get("review_detail") or {}
        result = await _finish(
            cid, "pending_review", ["待人工复核", "文化契合边缘"],
            f"待人工复核：文化契合 {detail.get('score', '?')} 分处于待复核区间，"
            f"综合评分 {overall}。系统不自动决策，请 HR 裁决。"
            + ("；已按配置跳过第三轮面试，面试均分仅由前两轮构成。"
               if settings.SKIP_ROUND3_ON_REVIEW else ""),
            "待人工复核", overall, state, "pending_review")
        return {**result, "final_decision": "待人工复核", "overall_score": overall}

    notes = f"待定沉淀：综合评分 {overall}，进入人才池持续跟进。{state.get('final_recommendation', {}).get('reasons', '')}"
    result = await _finish(cid, "talent_pool", ["待定", "潜力候选人"], notes,
                           "待定，进入人才池", overall, state, "talent_pool")
    return {**result, "final_decision": "待定，进入人才池", "overall_score": overall}


async def reject_to_pool(state: RecruitmentState) -> RecruitmentState:
    """淘汰分支同样沉淀人才池（带淘汰阶段标签），保证候选人资产不丢失"""
    cid = state["candidate_id"]
    step = state.get("current_step", "unknown")
    reason_map = {
        "assess_cultural_fit": "文化契合度不达标",
        "questionnaire_scored": "问卷测评不达标",
        "interview_1_done": "第一轮面试不通过",
        "interview_2_done": "第二轮面试不通过",
        "interview_3_done": "第三轮面试不通过",
        "generate_hiring_decision": "综合评审不推荐",
    }
    reason = reason_map.get(step, f"流程淘汰({step})")
    # 淘汰发生在评审前时，用与终局完全相同的公式估算综合分。
    # 旧实现在这里另写了一套权重（面试0.5/技能0.2/文化0.15/问卷0.15），
    # 漏掉经验与教育，并用 `or 60` 给未测评的问卷白送 60 分——
    # 既让两处综合分不可比，也让"没考"比"考砸了"得分高。现已统一。
    overall, assessed = compute_overall(state)
    interview_scores = state.get("interview_scores") or []
    decision = f"不推荐（{reason}）"
    notes = (f"淘汰沉淀：{reason}。评分快照：技能 {state.get('skill_match_score')}，"
             f"经验 {state.get('experience_score')}，教育 {state.get('education_score')}，"
             f"文化 {state.get('culture_match_score')}，问卷 {state.get('questionnaire_score')}，"
             f"面试 {interview_scores}；综合分基于 {assessed or '无已测评维度'}")
    result = await _finish(cid, "rejected", ["已淘汰", reason], notes,
                           decision, overall, state, "rejected")
    return {**result, "final_decision": decision, "overall_score": overall}


# ================================================================ 图组装

def build_recruitment_graph(checkpointer=None):
    workflow = StateGraph(RecruitmentState)

    workflow.add_node("parse_resume", parse_resume)
    workflow.add_node("extract_skills", extract_skills)
    workflow.add_node("evaluate_skill_match", evaluate_skill_match)
    workflow.add_node("evaluate_experience", evaluate_experience)
    workflow.add_node("evaluate_education", evaluate_education)
    workflow.add_node("assess_cultural_fit", assess_cultural_fit)
    workflow.add_node("questionnaire_stage", questionnaire_stage)
    workflow.add_node("interview_round_1", make_interview_node(1, 58, 63))
    workflow.add_node("interview_round_2", make_interview_node(2, 69, 74))
    workflow.add_node("interview_round_3", make_interview_node(3, 80, 85))
    workflow.add_node("generate_hiring_decision", generate_hiring_decision)
    workflow.add_node("finalize_hire", finalize_hire)
    workflow.add_node("move_to_talent_pool", move_to_talent_pool)
    workflow.add_node("reject_to_pool", reject_to_pool)

    workflow.set_entry_point("parse_resume")
    workflow.add_edge("parse_resume", "extract_skills")
    workflow.add_edge("extract_skills", "evaluate_skill_match")
    workflow.add_edge("evaluate_skill_match", "evaluate_experience")
    workflow.add_edge("evaluate_experience", "evaluate_education")
    workflow.add_edge("evaluate_education", "assess_cultural_fit")

    workflow.add_conditional_edges("assess_cultural_fit", check_culture_pass, {
        # review 与 pass 走同一条边：文化契合落在待复核区间时不阻塞流程，
        # 继续跑完问卷与面试，终局再锁档到"待人工复核"交给 HR 事后裁决
        "pass": "questionnaire_stage",
        "review": "questionnaire_stage",
        "reject": "reject_to_pool",
    })
    workflow.add_conditional_edges("questionnaire_stage", check_questionnaire_pass, {
        "pass": "interview_round_1",
        "reject": "reject_to_pool",
    })
    workflow.add_conditional_edges("interview_round_1", make_interview_check(1), {
        "pass": "interview_round_2",
        "reject": "reject_to_pool",
    })
    workflow.add_conditional_edges("interview_round_2", make_interview_check_with_r3_skip(2), {
        "pass": "interview_round_3",
        # 复核态且开关打开：直接进终局，省掉第三轮面试的排期与等待
        "skip_r3": "generate_hiring_decision",
        "reject": "reject_to_pool",
    })
    workflow.add_conditional_edges("interview_round_3", make_interview_check(3), {
        "pass": "generate_hiring_decision",
        "reject": "reject_to_pool",
    })
    workflow.add_conditional_edges("generate_hiring_decision", check_final_decision, {
        "hire": "finalize_hire",
        "pool": "move_to_talent_pool",
        "reject": "reject_to_pool",
    })

    workflow.add_edge("finalize_hire", END)
    workflow.add_edge("move_to_talent_pool", END)
    workflow.add_edge("reject_to_pool", END)

    return workflow.compile(checkpointer=checkpointer)
