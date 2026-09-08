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
from typing import TypedDict, List, Dict, Any, Optional

from langgraph.graph import StateGraph, END
from langgraph.types import interrupt
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from src.config import settings
from src.workflow import db_actions as db
from src.sse import notification as sse

_llm_instance: Optional[ChatOpenAI] = None


def _get_llm() -> ChatOpenAI:
    """延迟初始化 LLM，避免启动时无 API Key 报错。

    评分类任务要求低温度（0.1）保证同分输入结果稳定可复现，
    温度偏高会让同一候选人两次跑分差拉大，破坏鉴别力验证。
    """
    global _llm_instance
    if _llm_instance is None:
        if not settings.LLM_API_KEY:
            raise RuntimeError("LLM API Key 未配置")
        _llm_instance = ChatOpenAI(
            model=settings.LLM_MODEL,
            temperature=0.1,
            timeout=45,
            max_retries=2,
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )
    return _llm_instance


def _extract_llm_error(e: Exception) -> str:
    """从 langchain/openai 异常中提取 HTTP 状态码与响应体 message，

    避免把 401(key 错)、404(模型名错)、429(限流)、超时 全部吞成同一句"调用失败"。
    """
    status = getattr(getattr(e, "response", None), "status_code", None)
    body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            import json as _json
            body = _json.loads(resp.text).get("error", {}).get("message", "")[:200]
        except Exception:
            body = str(getattr(resp, "text", ""))[:200]
    etype = type(e).__name__
    return f"[{etype}] HTTP {status or '?'} {body or str(e)[:200]}"


async def _llm_json(prompt_text: str, variables: Dict[str, Any], default: Dict[str, Any]) -> Dict[str, Any]:
    """LLM JSON 调用统一入口：线程池执行避免阻塞事件循环，失败/无 Key 时降级返回 default"""
    if not settings.LLM_API_KEY:
        return default

    def _invoke() -> Dict[str, Any]:
        prompt = PromptTemplate(template=prompt_text, input_variables=list(variables.keys()))
        chain = prompt | _get_llm() | JsonOutputParser()
        return chain.invoke(variables)

    try:
        return await asyncio.to_thread(_invoke)
    except Exception as e:
        # 错误透传：明确打印根因，便于区分 key/模型名/限流/超时
        print(f"[workflow] LLM 调用失败，降级为规则结果: {_extract_llm_error(e)}")
        return default


class RecruitmentState(TypedDict, total=False):
    candidate_id: int
    candidate_name: str
    resume_text: str
    position_requirements: str
    position: str
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

async def parse_resume(state: RecruitmentState) -> RecruitmentState:
    cid = state["candidate_id"]
    # 幂等：上传环节或此前运行已解析过则直接复用
    parsed = db.get_resume_parsed(cid)
    if not parsed:
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
        )
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
    )
    merged = {**parsed, **skills_data}
    db.save_resume_parsed(state["candidate_id"], merged, skills=skills_data.get("skills_technical"))
    return {**state, "parsed_resume": merged, "workflow_progress": 10, "current_step": "extract_skills"}


# ================================================================ 阶段二：人岗匹配评估

async def evaluate_skill_match(state: RecruitmentState) -> RecruitmentState:
    parsed = state.get("parsed_resume") or {}
    result = await _llm_json(
        """你是严格的招聘技术评审。评估候选人「技能」与职位要求的匹配度。

候选人技能清单：{skills}
技能使用佐证（工作经历与项目）：{evidence}
职位要求：{position_requirements}

评分锚点（必须严格遵守）：
- 90-100：职位要求的核心技能全部命中，且多数能在工作经历/项目中找到实际使用与成果佐证
- 75-89：核心技能基本命中，1-2 项次要技能缺失，或部分技能缺少佐证
- 60-74：核心技能命中一半左右，或技能仅出现在清单里、经历中无使用痕迹
- 40-59：多项核心技能缺失
- 0-39：核心技能基本不具备

硬性规则：
1. 技能是否命中只看技能清单与经历中「明确出现」的内容，不得推测、脑补候选人可能具备的技能。
2. 命中但无使用佐证的技能，按锚点降档处理，不得仅凭清单给 90+。
3. 先逐条列出职位要求的核心技能，逐条核对命中情况与佐证，再给分。
4. 评分必须拉开差距，证据不足时宁可给低分，禁止全员 70-85 的趋中打分。

输出JSON：{{"required_core_skills": [...], "matched_skills": [...], "missing_skills": [...], "score": number, "reason": "一句话说明扣分点"}}""",
        {
            "skills": str(parsed.get("skills_technical") or parsed.get("skills", [])),
            "evidence": str({"experience": parsed.get("experience", []),
                             "projects": parsed.get("projects", [])})[:3500],
            "position_requirements": state.get("position_requirements") or state.get("position") or "通用岗位要求",
        },
        {"matched_skills": [], "missing_skills": [], "score": 70},
    )
    score = float(result.get("score", 70))
    db.upsert_evaluation(state["candidate_id"], "技能匹配", score,
                         f"匹配: {','.join(result.get('matched_skills', [])[:6])}；{result.get('reason', '')}")
    return {**state, "skill_match_score": score, "skill_match_details": result,
            "workflow_progress": 18, "current_step": "evaluate_skill_match"}


async def evaluate_experience(state: RecruitmentState) -> RecruitmentState:
    parsed = state.get("parsed_resume") or {}
    result = await _llm_json(
        """你是严格的招聘评审。评估候选人「工作经验」与职位的匹配度。

候选人经验：{experience}
职位要求：{position_requirements}

评分锚点（必须严格遵守）：
- 90-100：同岗位经验 5 年以上，有与职位高度相关的主导项目且成果可量化
- 75-89：同岗位经验 3-5 年，项目相关度高
- 60-74：经验年限达标但岗位/行业相关度一般，或成果描述空泛
- 40-59：相关经验不足 2 年，或主要为不相关岗位
- 0-39：无相关经验或简历未提供有效经历

硬性规则：
1. 只依据简历中明确写出的公司、岗位、年限、项目成果打分，不得脑补。
2. 年限不足、行业不相关、成果无量化证据都必须显著扣分。
3. 禁止趋中打分，强弱候选人必须拉开分差。

输出JSON：{{"years_relevant": number, "analysis": "依据简历事实的分析，引用具体经历", "score": number}}""",
        {
            "experience": str(parsed.get("experience", []))[:3000],
            "position_requirements": state.get("position_requirements") or state.get("position") or "通用岗位要求",
        },
        {"analysis": "经验评估降级", "score": 70},
    )
    score = float(result.get("score", 70))
    db.upsert_evaluation(state["candidate_id"], "经验匹配", score, result.get("analysis", ""))
    return {**state, "experience_score": score,
            "skill_match_details": {**(state.get("skill_match_details") or {}), "experience_analysis": result},
            "workflow_progress": 26, "current_step": "evaluate_experience"}


async def evaluate_education(state: RecruitmentState) -> RecruitmentState:
    parsed = state.get("parsed_resume") or {}
    result = await _llm_json(
        """你是严格的招聘评审。评估候选人「教育背景」与职位的匹配度。

候选人教育背景：{education}
职位要求：{position_requirements}

评分锚点（必须严格遵守）：
- 90-100：本科及以上，专业与职位高度对口（如计算机/软件工程对口研发岗）
- 75-89：本科及以上，专业相近或有相关辅修/认证
- 60-74：本科但专业不相关，或大专但专业高度对口
- 40-59：大专且专业不相关
- 0-39：高中及以下，或简历未提供教育信息

硬性规则：
1. 只依据简历明确写出的学历层次、院校、专业打分。
2. 简历未提供教育信息时给 40 分以下，不得默认本科。
3. 禁止趋中打分。

输出JSON：{{"analysis": "引用简历中的具体学历/专业事实", "score": number}}""",
        {
            "education": str(parsed.get("education", []))[:2000],
            "position_requirements": state.get("position_requirements") or state.get("position") or "通用岗位要求",
        },
        {"analysis": "教育评估降级", "score": 70},
    )
    score = float(result.get("score", 70))
    db.upsert_evaluation(state["candidate_id"], "教育匹配", score, result.get("analysis", ""))
    return {**state, "education_score": score,
            "skill_match_details": {**(state.get("skill_match_details") or {}), "education_analysis": result},
            "workflow_progress": 33, "current_step": "evaluate_education"}


async def assess_cultural_fit(state: RecruitmentState) -> RecruitmentState:
    result = await _llm_json(
        """你是严格的招聘评审。基于简历中「可见的客观证据」评估文化匹配度。

公司文化价值观：创新精神、团队协作、客户导向、学习能力、责任心。
候选人简历信息：{parsed_resume}

可作为证据的客观事实：跨部门/团队协作项目、开源或技术分享、长期稳定任职、
客户对接或乙方经历、持续学习痕迹（认证/自学/跨领域）、主导并交付的责任成果。
简历中没有的内容一律视为证据缺失，不得凭空推测候选人"应该具备"某素质。

评分锚点（必须严格遵守）：
- 85-100：简历中能找到 3 项以上价值观的明确事实证据
- 70-84：能找到 2 项明确证据
- 55-69：仅 1 项证据或证据间接
- 40-54：简历未提供任何有效文化证据（信息不足的默认档）
- 0-39：简历中有负面信号（如频繁跳槽且无合理解释）

输出JSON：{{"evidence": ["逐条列出简历中的具体事实，注明对应哪项价值观"], "score": number, "reasons": ["扣分理由，明确指出哪些价值观无证据"]}}""",
        {"parsed_resume": str(state.get("parsed_resume") or {})[:3000]},
        {"evidence": [], "score": 70, "reasons": ["文化评估降级，默认通过"]},
    )
    score = float(result.get("score", 70))
    db.upsert_evaluation(state["candidate_id"], "文化契合", score,
                         "；".join(result.get("reasons", []))[:300])
    return {**state, "culture_match_score": score, "culture_match_details": result,
            "workflow_progress": 40, "current_step": "assess_cultural_fit"}


def check_culture_pass(state: RecruitmentState) -> str:
    if state.get("culture_match_score", 0) >= 60:
        return "pass"
    return "reject"


# ================================================================ 阶段三：自动问询筛选（挂起点 1）

async def questionnaire_stage(state: RecruitmentState) -> RecruitmentState:
    """生成问卷并写库 → interrupt 挂起等待作答 → 恢复后对真实答案评分"""
    cid = state["candidate_id"]
    db.set_candidate_status(cid, "questionnaire")

    q_name = f"AI测评卷-{cid}-{state.get('candidate_name', '候选人')}"
    gen = await _llm_json(
        """根据职位要求和候选人简历生成测评问卷。

职位要求：{position_requirements}
候选人简历摘要：{resume_summary}

生成5个测评问题，涵盖核心技术栈、架构设计、问题解决能力。
输出JSON：{{"questions": [{{"question": "...", "type": "问答题"}}]}}""",
        {
            "position_requirements": state.get("position_requirements") or state.get("position") or "通用岗位要求",
            "resume_summary": str(state.get("parsed_resume") or {})[:2000],
        },
        {"questions": [
            {"question": "请描述你最具代表性的项目及你在其中的职责", "type": "问答题"},
            {"question": "你如何处理线上紧急故障？请举例说明", "type": "问答题"},
            {"question": "你最擅长的技术栈是什么？如何保持技术更新？", "type": "问答题"},
        ]},
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
        """你是严格的招聘测评官，根据职位要求对候选人问卷答案评分。

职位要求：{position_requirements}
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
            "position_requirements": state.get("position_requirements") or state.get("position") or "",
            "questions": str(questions)[:2000],
            "responses": str(responses)[:4000],
        },
        # 降级：按作答完整度给 60-90 分
        {"score": int(60 + 30 * answered / total), "feedback": "规则评分：按作答完整度估算"},
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


# ================================================================ 阶段五：综合评审与决策

async def generate_hiring_decision(state: RecruitmentState) -> RecruitmentState:
    cid = state["candidate_id"]
    interview_scores = state.get("interview_scores") or []
    interview_avg = (sum(s["score"] for s in interview_scores) / len(interview_scores)) if interview_scores else 0

    # 综合分由规则加权确定性计算（LLM 做加权算术不可靠，会与子项自相矛盾）。
    # 权重设计：面试最强信号，文化从简历推断最不靠谱故权重最低。
    skill = state.get("skill_match_score", 0) or 0
    experience = state.get("experience_score", 0) or 0
    education = state.get("education_score", 0) or 0
    culture = state.get("culture_match_score", 0) or 0
    questionnaire = state.get("questionnaire_score", 0) or 0
    overall = int(
        interview_avg * 0.35 + skill * 0.20 + experience * 0.15
        + questionnaire * 0.15 + education * 0.10 + culture * 0.05
    )
    # 硬性否决：任一核心维度低于 50，综合分封顶 79（最高只能"待定"，
    # 落入人才池；封顶后仍低于 65 则维持"不推荐"）
    hard_veto = any(x < 50 for x in (skill, questionnaire) if x > 0)
    capped = min(overall, 79) if hard_veto else overall
    if capped >= 80:
        rule_decision = "推荐录用"
    elif capped >= 65:
        rule_decision = "待定"
    else:
        rule_decision = "不推荐"

    decision = await _llm_json(
        """你是招聘委员会负责人，基于各维度评分做出最终录用决策。

候选人：{candidate_name}
各维度分数：技能匹配 {skill}、经验匹配 {experience}、教育匹配 {education}、
文化契合 {culture}、问卷测评 {questionnaire}、面试均分 {interview_avg}（各轮明细：{interviews}）
系统按权重算出的综合分：{overall}；规则档位建议：{rule_decision}。

决策要求：
1. 综合分以系统计算的 {overall} 为准，你不要自行改分。
2. 决策档位原则上与规则档位一致；仅当存在明确硬伤（如核心维度低于 50、面试反馈差）
   时可下调一档，或有突出亮点（多项 90+）时上调一档，并必须在理由中说明依据。
3. 理由要引用具体维度分数和事实，禁止空话。

输出JSON：{{"decision": "推荐录用|待定|不推荐", "reasons": ["3条以内，引用具体分数和事实"], "risks": ["录用后需关注的风险，无则空数组"]}}""",
        {
            "candidate_name": state.get("candidate_name", ""),
            "skill": int(skill), "experience": int(experience), "education": int(education),
            "culture": int(culture), "questionnaire": int(questionnaire),
            "interview_avg": round(interview_avg, 1),
            "interviews": str(interview_scores),
            "overall": capped, "rule_decision": rule_decision,
        },
        {"decision": rule_decision, "reasons": [f"规则综合评分 {capped} 分，档位 {rule_decision}"], "risks": []},
    )

    final_decision = decision.get("decision") or rule_decision
    decision["overall_score"] = capped  # 综合分始终以规则计算为准
    db.upsert_evaluation(cid, "综合评审", capped, "；".join(decision.get("reasons", []))[:300])

    return {**state, "final_decision": final_decision,
            "final_recommendation": decision, "overall_score": capped,
            "workflow_progress": 92, "current_step": "generate_hiring_decision"}


def check_final_decision(state: RecruitmentState) -> str:
    decision = state.get("final_decision") or ""
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
    # 淘汰发生在评审前时，按已产生的分数估算综合分
    interview_scores = state.get("interview_scores") or []
    interview_avg = (sum(s["score"] for s in interview_scores) / len(interview_scores)) if interview_scores else 0
    overall = state.get("overall_score") or int(
        interview_avg * 0.5
        + (state.get("skill_match_score") or 70) * 0.2
        + (state.get("culture_match_score") or 70) * 0.15
        + (state.get("questionnaire_score") or 60) * 0.15
    )
    decision = f"不推荐（{reason}）"
    notes = f"淘汰沉淀：{reason}。评分快照：技能 {state.get('skill_match_score')}，文化 {state.get('culture_match_score')}，问卷 {state.get('questionnaire_score')}，面试 {interview_scores}"
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
        "pass": "questionnaire_stage",
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
    workflow.add_conditional_edges("interview_round_2", make_interview_check(2), {
        "pass": "interview_round_3",
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
