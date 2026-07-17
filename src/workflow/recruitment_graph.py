from typing import TypedDict, List, Dict, Any, Optional
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from src.config import settings

llm = ChatOpenAI(
    model=settings.LLM_MODEL,
    temperature=0.3,
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL
)


class RecruitmentState(TypedDict):
    candidate_id: int
    candidate_name: str
    resume_text: str
    position_requirements: str
    parsed_resume: Optional[Dict[str, Any]] = None
    skill_match_score: Optional[float] = None
    skill_match_details: Optional[Dict[str, Any]] = None
    culture_match_score: Optional[float] = None
    culture_match_details: Optional[Dict[str, Any]] = None
    communication_score: Optional[float] = None
    questionnaire_generated: Optional[bool] = False
    questionnaire_id: Optional[int] = None
    questionnaire_score: Optional[int] = None
    interview_scheduled: Optional[bool] = False
    interview_round: Optional[int] = 0
    interview_scores: Optional[List[Dict[str, Any]]] = None
    final_decision: Optional[str] = None
    final_recommendation: Optional[str] = None
    workflow_progress: int = 0
    current_step: str = "start"


def parse_resume(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""解析以下简历内容，提取关键信息并结构化输出。

简历内容：
{resume_text}

请提取以下信息：
1. 基本信息：姓名、联系方式、邮箱
2. 教育背景：学历、专业、毕业院校、毕业时间
3. 工作经历：公司、职位、工作时间、主要职责
4. 技能清单：技术技能、软技能
5. 项目经验：项目名称、技术栈、贡献
6. 语言能力：语言、水平

请以JSON格式输出，键名为：basic_info, education, experience, skills, projects, language""",
        input_variables=["resume_text"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    parsed_data = chain.invoke({"resume_text": state["resume_text"]})
    
    return {
        **state,
        "parsed_resume": parsed_data,
        "workflow_progress": 5,
        "current_step": "parse_resume"
    }


def extract_skills(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""从以下解析后的简历中提取技能信息，按类别分类。

解析后的简历：
{parsed_resume}

请提取：
1. 技术技能（编程语言、框架、工具）
2. 软技能（沟通、团队协作、项目管理等）
3. 领域知识（行业经验、业务理解）

输出格式为JSON，包含skills_technical, skills_soft, domain_knowledge三个数组字段。""",
        input_variables=["parsed_resume"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    skills_data = chain.invoke({"parsed_resume": state["parsed_resume"]})
    
    return {
        **state,
        "parsed_resume": {**state["parsed_resume"], **skills_data},
        "workflow_progress": 10,
        "current_step": "extract_skills"
    }


def evaluate_skill_match(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""评估候选人技能与职位要求的匹配度。

候选人技能：
{skills}

职位要求：
{position_requirements}

请：
1. 列出匹配的技能
2. 列出缺失的技能
3. 给出技能匹配分数（0-100）

输出JSON格式：{{"matched_skills": [...], "missing_skills": [...], "score": number}}""",
        input_variables=["skills", "position_requirements"]
    )
    
    skills_str = str(state["parsed_resume"].get("skills", {}) or state["parsed_resume"].get("skills_technical", []))
    chain = prompt | llm | JsonOutputParser()
    match_result = chain.invoke({
        "skills": skills_str,
        "position_requirements": state["position_requirements"]
    })
    
    return {
        **state,
        "skill_match_score": match_result.get("score", 0),
        "skill_match_details": match_result,
        "workflow_progress": 15,
        "current_step": "evaluate_skill_match"
    }


def evaluate_experience(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""评估候选人工作经验与职位的匹配度。

候选人经验：
{experience}

职位要求：
{position_requirements}

请：
1. 分析相关工作经验
2. 评估项目经验的深度和广度
3. 给出经验匹配分数（0-100）

输出JSON格式：{{"analysis": "...", "depth_rating": number, "breadth_rating": number, "score": number}}""",
        input_variables=["experience", "position_requirements"]
    )
    
    experience_str = str(state["parsed_resume"].get("experience", []))
    chain = prompt | llm | JsonOutputParser()
    exp_result = chain.invoke({
        "experience": experience_str,
        "position_requirements": state["position_requirements"]
    })
    
    return {
        **state,
        "skill_match_score": (state.get("skill_match_score", 0) + exp_result.get("score", 0)) / 2,
        "skill_match_details": {
            **state.get("skill_match_details", {}),
            "experience_analysis": exp_result
        },
        "workflow_progress": 20,
        "current_step": "evaluate_experience"
    }


def evaluate_education(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""评估候选人教育背景与职位的匹配度。

候选人教育背景：
{education}

职位要求：
{position_requirements}

请：
1. 分析学历是否符合要求
2. 分析专业是否相关
3. 给出教育匹配分数（0-100）

输出JSON格式：{{"analysis": "...", "degree_match": boolean, "major_relevant": boolean, "score": number}}""",
        input_variables=["education", "position_requirements"]
    )
    
    education_str = str(state["parsed_resume"].get("education", []))
    chain = prompt | llm | JsonOutputParser()
    edu_result = chain.invoke({
        "education": education_str,
        "position_requirements": state["position_requirements"]
    })
    
    return {
        **state,
        "skill_match_score": (state.get("skill_match_score", 0) * 2 + edu_result.get("score", 0)) / 3,
        "skill_match_details": {
            **state.get("skill_match_details", {}),
            "education_analysis": edu_result
        },
        "workflow_progress": 25,
        "current_step": "evaluate_education"
    }


def assess_cultural_fit(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""基于简历内容评估候选人与公司文化的匹配度。

公司文化价值观：
- 创新精神：勇于尝试新技术，推动变革
- 团队协作：善于与团队沟通，共同解决问题
- 客户导向：关注客户需求，提供优质服务
- 学习能力：持续学习，适应变化
- 责任心：对工作负责，追求卓越

候选人信息：
{parsed_resume}

请：
1. 从简历中寻找体现公司价值观的证据
2. 评估文化匹配度（0-100）
3. 给出具体理由

输出JSON格式：{{"evidence": [...], "score": number, "reasons": [...]}}""",
        input_variables=["parsed_resume"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    culture_result = chain.invoke({"parsed_resume": state["parsed_resume"]})
    
    return {
        **state,
        "culture_match_score": culture_result.get("score", 0),
        "culture_match_details": culture_result,
        "workflow_progress": 30,
        "current_step": "assess_cultural_fit"
    }


def check_culture_pass(state: RecruitmentState) -> str:
    if state.get("culture_match_score", 0) >= 60:
        return "pass"
    return "reject"


def generate_technical_questionnaire(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""根据职位要求和候选人简历生成技术问卷。

职位要求：
{position_requirements}

候选人简历摘要：
{resume_summary}

请生成5-8个技术问题，涵盖：
1. 核心技术栈相关问题
2. 架构设计问题
3. 问题解决能力问题

输出JSON格式：{{"questions": [{{"question": "...", "type": "选择题|问答题|编程题", "options": [...]}}]}}""",
        input_variables=["position_requirements", "resume_summary"]
    )
    
    resume_summary = str({
        "skills": state["parsed_resume"].get("skills", {}),
        "experience": state["parsed_resume"].get("experience", [])[:2]
    })
    
    chain = prompt | llm | JsonOutputParser()
    questions = chain.invoke({
        "position_requirements": state["position_requirements"],
        "resume_summary": resume_summary
    })
    
    return {
        **state,
        "questionnaire_generated": True,
        "questionnaire_id": 1,
        "workflow_progress": 35,
        "current_step": "generate_technical_questionnaire"
    }


def generate_behavioral_questionnaire(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""根据职位要求生成行为面试问题。

职位要求：
{position_requirements}

请生成5个行为面试问题，考察：
1. 团队协作能力
2. 问题解决能力
3. 压力应对能力
4. 职业发展规划
5. 文化适应能力

输出JSON格式：{{"questions": [{{"question": "...", "ideal_answer": "..."}}]}}""",
        input_variables=["position_requirements"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    questions = chain.invoke({"position_requirements": state["position_requirements"]})
    
    return {
        **state,
        "questionnaire_generated": True,
        "questionnaire_id": 2,
        "workflow_progress": 40,
        "current_step": "generate_behavioral_questionnaire"
    }


def evaluate_questionnaire(state: RecruitmentState) -> RecruitmentState:
    return {
        **state,
        "questionnaire_score": 85,
        "workflow_progress": 45,
        "current_step": "evaluate_questionnaire"
    }


def check_questionnaire_pass(state: RecruitmentState) -> str:
    if state.get("questionnaire_score", 0) >= 60:
        return "pass"
    return "reject"


def schedule_first_interview(state: RecruitmentState) -> RecruitmentState:
    return {
        **state,
        "interview_scheduled": True,
        "interview_round": 1,
        "workflow_progress": 50,
        "current_step": "schedule_first_interview"
    }


def conduct_first_interview(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""模拟第一轮技术面试评估。

候选人信息：
{parsed_resume}

职位要求：
{position_requirements}

请从以下维度评估：
1. 技术能力（0-100）
2. 沟通能力（0-100）
3. 问题解决能力（0-100）

输出JSON格式：{{"technical_score": number, "communication_score": number, "problem_solving_score": number, "feedback": "..."}}""",
        input_variables=["parsed_resume", "position_requirements"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    result = chain.invoke({
        "parsed_resume": state["parsed_resume"],
        "position_requirements": state["position_requirements"]
    })
    
    return {
        **state,
        "interview_scores": [result],
        "communication_score": result.get("communication_score", 0),
        "workflow_progress": 55,
        "current_step": "conduct_first_interview"
    }


def check_first_interview_pass(state: RecruitmentState) -> str:
    scores = state.get("interview_scores", [])
    if scores:
        avg_score = sum(scores[0].values()) / len(scores[0])
        if avg_score >= 70:
            return "pass"
    return "reject"


def schedule_second_interview(state: RecruitmentState) -> RecruitmentState:
    return {
        **state,
        "interview_round": 2,
        "workflow_progress": 60,
        "current_step": "schedule_second_interview"
    }


def conduct_second_interview(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""模拟第二轮综合面试评估。

候选人信息：
{parsed_resume}

职位要求：
{position_requirements}

请从以下维度评估：
1. 文化契合度（0-100）
2. 领导力潜力（0-100）
3. 职业规划匹配度（0-100）
4. 薪资期望匹配度（0-100）

输出JSON格式：{{"culture_score": number, "leadership_score": number, "career_fit_score": number, "salary_fit_score": number, "feedback": "..."}}""",
        input_variables=["parsed_resume", "position_requirements"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    result = chain.invoke({
        "parsed_resume": state["parsed_resume"],
        "position_requirements": state["position_requirements"]
    })
    
    return {
        **state,
        "interview_scores": state.get("interview_scores", []) + [result],
        "workflow_progress": 65,
        "current_step": "conduct_second_interview"
    }


def check_second_interview_pass(state: RecruitmentState) -> str:
    scores = state.get("interview_scores", [])
    if len(scores) >= 2:
        total_score = 0
        count = 0
        for s in scores:
            total_score += sum(v for v in s.values() if isinstance(v, (int, float)))
            count += len([v for v in s.values() if isinstance(v, (int, float))])
        avg_score = total_score / count if count > 0 else 0
        if avg_score >= 70:
            return "pass"
    return "reject"


def schedule_third_interview(state: RecruitmentState) -> RecruitmentState:
    return {
        **state,
        "interview_round": 3,
        "workflow_progress": 70,
        "current_step": "schedule_third_interview"
    }


def conduct_third_interview(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""模拟第三轮HR面试评估。

候选人信息：
{parsed_resume}

职位要求：
{position_requirements}

请从以下维度评估：
1. 薪资期望（0-100）
2. 入职时间（0-100）
3. 职业稳定性（0-100）
4. 团队合作意愿（0-100）

输出JSON格式：{{"salary_expectation": number, "onboarding_time": number, "stability": number, "team_willingness": number, "feedback": "..."}}""",
        input_variables=["parsed_resume", "position_requirements"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    result = chain.invoke({
        "parsed_resume": state["parsed_resume"],
        "position_requirements": state["position_requirements"]
    })
    
    return {
        **state,
        "interview_scores": state.get("interview_scores", []) + [result],
        "workflow_progress": 75,
        "current_step": "conduct_third_interview"
    }


def check_third_interview_pass(state: RecruitmentState) -> str:
    scores = state.get("interview_scores", [])
    if len(scores) >= 3:
        total_score = 0
        count = 0
        for s in scores:
            total_score += sum(v for v in s.values() if isinstance(v, (int, float)))
            count += len([v for v in s.values() if isinstance(v, (int, float))])
        avg_score = total_score / count if count > 0 else 0
        if avg_score >= 70:
            return "pass"
    return "reject"


def generate_hiring_decision(state: RecruitmentState) -> RecruitmentState:
    prompt = PromptTemplate(
        template="""基于所有评估数据做出最终招聘决策。

候选人：{candidate_name}

评估数据：
- 技能匹配分数：{skill_match_score}
- 文化匹配分数：{culture_match_score}
- 沟通能力分数：{communication_score}
- 问卷分数：{questionnaire_score}
- 面试分数：{interview_scores}

请：
1. 计算综合评分
2. 做出招聘决策（推荐录用/待定/不推荐）
3. 给出详细理由

输出JSON格式：{{"overall_score": number, "decision": "推荐录用|待定|不推荐", "reasons": [...]}}""",
        input_variables=["candidate_name", "skill_match_score", "culture_match_score", "communication_score", "questionnaire_score", "interview_scores"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    decision = chain.invoke({
        "candidate_name": state["candidate_name"],
        "skill_match_score": state.get("skill_match_score", 0),
        "culture_match_score": state.get("culture_match_score", 0),
        "communication_score": state.get("communication_score", 0),
        "questionnaire_score": state.get("questionnaire_score", 0),
        "interview_scores": state.get("interview_scores", [])
    })
    
    return {
        **state,
        "final_decision": decision.get("decision"),
        "final_recommendation": decision,
        "workflow_progress": 90,
        "current_step": "generate_hiring_decision"
    }


def finalize_candidate(state: RecruitmentState) -> RecruitmentState:
    return {
        **state,
        "workflow_progress": 100,
        "current_step": "finalize_candidate"
    }


def build_recruitment_graph():
    workflow = StateGraph(RecruitmentState)
    
    workflow.add_node("parse_resume", parse_resume)
    workflow.add_node("extract_skills", extract_skills)
    workflow.add_node("evaluate_skill_match", evaluate_skill_match)
    workflow.add_node("evaluate_experience", evaluate_experience)
    workflow.add_node("evaluate_education", evaluate_education)
    workflow.add_node("assess_cultural_fit", assess_cultural_fit)
    workflow.add_node("generate_technical_questionnaire", generate_technical_questionnaire)
    workflow.add_node("generate_behavioral_questionnaire", generate_behavioral_questionnaire)
    workflow.add_node("evaluate_questionnaire", evaluate_questionnaire)
    workflow.add_node("schedule_first_interview", schedule_first_interview)
    workflow.add_node("conduct_first_interview", conduct_first_interview)
    workflow.add_node("schedule_second_interview", schedule_second_interview)
    workflow.add_node("conduct_second_interview", conduct_second_interview)
    workflow.add_node("schedule_third_interview", schedule_third_interview)
    workflow.add_node("conduct_third_interview", conduct_third_interview)
    workflow.add_node("generate_hiring_decision", generate_hiring_decision)
    workflow.add_node("finalize_candidate", finalize_candidate)
    
    workflow.set_entry_point("parse_resume")
    
    workflow.add_edge("parse_resume", "extract_skills")
    workflow.add_edge("extract_skills", "evaluate_skill_match")
    workflow.add_edge("evaluate_skill_match", "evaluate_experience")
    workflow.add_edge("evaluate_experience", "evaluate_education")
    workflow.add_edge("evaluate_education", "assess_cultural_fit")
    
    workflow.add_conditional_edges(
        "assess_cultural_fit",
        check_culture_pass,
        {
            "pass": "generate_technical_questionnaire",
            "reject": END
        }
    )
    
    workflow.add_edge("generate_technical_questionnaire", "generate_behavioral_questionnaire")
    workflow.add_edge("generate_behavioral_questionnaire", "evaluate_questionnaire")
    
    workflow.add_conditional_edges(
        "evaluate_questionnaire",
        check_questionnaire_pass,
        {
            "pass": "schedule_first_interview",
            "reject": END
        }
    )
    
    workflow.add_edge("schedule_first_interview", "conduct_first_interview")
    
    workflow.add_conditional_edges(
        "conduct_first_interview",
        check_first_interview_pass,
        {
            "pass": "schedule_second_interview",
            "reject": END
        }
    )
    
    workflow.add_edge("schedule_second_interview", "conduct_second_interview")
    
    workflow.add_conditional_edges(
        "conduct_second_interview",
        check_second_interview_pass,
        {
            "pass": "schedule_third_interview",
            "reject": END
        }
    )
    
    workflow.add_edge("schedule_third_interview", "conduct_third_interview")
    
    workflow.add_conditional_edges(
        "conduct_third_interview",
        check_third_interview_pass,
        {
            "pass": "generate_hiring_decision",
            "reject": END
        }
    )
    
    workflow.add_edge("generate_hiring_decision", "finalize_candidate")
    workflow.add_edge("finalize_candidate", END)
    
    return workflow.compile()


recruitment_graph = build_recruitment_graph()
