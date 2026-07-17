from typing import Optional, List, Dict, Any
from mcp import register_tool
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from src.config import settings
from src.models.database import get_db, Candidate, Resume, Interview, Questionnaire, Evaluation, TalentPool

llm = ChatOpenAI(
    model=settings.LLM_MODEL,
    temperature=0.3,
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL
)


@register_tool(
    name="parse_resume",
    description="解析简历内容，提取结构化信息",
    parameters={
        "resume_text": {"type": "string", "description": "简历文本内容"}
    }
)
def parse_resume(resume_text: str) -> Dict[str, Any]:
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
    return chain.invoke({"resume_text": resume_text})


@register_tool(
    name="search_candidates",
    description="根据条件搜索候选人",
    parameters={
        "keyword": {"type": "string", "description": "搜索关键词"},
        "position": {"type": "string", "description": "目标职位", "optional": True},
        "skill": {"type": "string", "description": "技能要求", "optional": True}
    }
)
def search_candidates(keyword: str, position: Optional[str] = None, skill: Optional[str] = None) -> List[Dict[str, Any]]:
    db = next(get_db())
    query = db.query(Candidate)
    
    if keyword:
        query = query.filter(
            Candidate.name.contains(keyword) | 
            Candidate.resume_text.contains(keyword)
        )
    if position:
        query = query.filter(Candidate.position == position)
    
    candidates = query.all()
    result = []
    for candidate in candidates:
        result.append({
            "id": candidate.id,
            "name": candidate.name,
            "email": candidate.email,
            "phone": candidate.phone,
            "position": candidate.position,
            "status": candidate.status,
            "created_at": candidate.created_at.isoformat()
        })
    return result


@register_tool(
    name="schedule_interview",
    description="安排面试",
    parameters={
        "candidate_id": {"type": "integer", "description": "候选人ID"},
        "interviewer_id": {"type": "integer", "description": "面试官ID"},
        "position": {"type": "string", "description": "职位名称"},
        "round": {"type": "integer", "description": "面试轮次"},
        "scheduled_at": {"type": "string", "description": "安排时间(ISO格式)"}
    }
)
def schedule_interview(
    candidate_id: int,
    interviewer_id: int,
    position: str,
    round: int = 1,
    scheduled_at: Optional[str] = None
) -> Dict[str, Any]:
    db = next(get_db())
    
    interview = Interview(
        candidate_id=candidate_id,
        position=position,
        round=round,
        interviewer_id=interviewer_id,
        scheduled_at=scheduled_at
    )
    db.add(interview)
    db.commit()
    db.refresh(interview)
    
    return {
        "id": interview.id,
        "candidate_id": interview.candidate_id,
        "position": interview.position,
        "round": interview.round,
        "status": interview.status,
        "scheduled_at": interview.scheduled_at.isoformat() if interview.scheduled_at else None
    }


@register_tool(
    name="create_questionnaire",
    description="创建问卷",
    parameters={
        "name": {"type": "string", "description": "问卷名称"},
        "type": {"type": "string", "description": "问卷类型(technical/behavioral)"},
        "position_requirements": {"type": "string", "description": "职位要求"}
    }
)
def create_questionnaire(name: str, type: str, position_requirements: str) -> Dict[str, Any]:
    prompt = PromptTemplate(
        template="""根据职位要求生成问卷。

问卷名称：{name}
问卷类型：{type}
职位要求：{position_requirements}

请生成5-8个问题：
- 如果是technical类型：生成技术问题，包含选择题和问答题
- 如果是behavioral类型：生成行为面试问题

输出JSON格式：{{"questions": [{{"question": "...", "type": "...", "options": [...]}}]}}""",
        input_variables=["name", "type", "position_requirements"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    questions = chain.invoke({
        "name": name,
        "type": type,
        "position_requirements": position_requirements
    })
    
    db = next(get_db())
    questionnaire = Questionnaire(
        name=name,
        type=type,
        questions=questions
    )
    db.add(questionnaire)
    db.commit()
    db.refresh(questionnaire)
    
    return {
        "id": questionnaire.id,
        "name": questionnaire.name,
        "type": questionnaire.type,
        "questions": questions
    }


@register_tool(
    name="evaluate_candidate",
    description="评估候选人",
    parameters={
        "candidate_id": {"type": "integer", "description": "候选人ID"},
        "evaluator_id": {"type": "integer", "description": "评估人ID"},
        "dimension": {"type": "string", "description": "评估维度"},
        "score": {"type": "integer", "description": "评分(0-100)"},
        "comment": {"type": "string", "description": "评估评语", "optional": True}
    }
)
def evaluate_candidate(
    candidate_id: int,
    evaluator_id: int,
    dimension: str,
    score: int,
    comment: Optional[str] = None
) -> Dict[str, Any]:
    db = next(get_db())
    
    evaluation = Evaluation(
        candidate_id=candidate_id,
        evaluator_id=evaluator_id,
        dimension=dimension,
        score=score,
        comment=comment
    )
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)
    
    return {
        "id": evaluation.id,
        "candidate_id": evaluation.candidate_id,
        "dimension": evaluation.dimension,
        "score": evaluation.score,
        "comment": evaluation.comment
    }


@register_tool(
    name="add_to_talent_pool",
    description="添加候选人到人才池",
    parameters={
        "candidate_id": {"type": "integer", "description": "候选人ID"},
        "tags": {"type": "array", "description": "标签列表"},
        "notes": {"type": "string", "description": "备注信息", "optional": True}
    }
)
def add_to_talent_pool(
    candidate_id: int,
    tags: Optional[List[str]] = None,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    db = next(get_db())
    
    talent_pool = TalentPool(
        candidate_id=candidate_id,
        tags=tags or [],
        notes=notes
    )
    db.add(talent_pool)
    db.commit()
    db.refresh(talent_pool)
    
    return {
        "id": talent_pool.id,
        "candidate_id": talent_pool.candidate_id,
        "status": talent_pool.status,
        "tags": talent_pool.tags,
        "notes": talent_pool.notes
    }


@register_tool(
    name="generate_interview_questions",
    description="生成面试问题",
    parameters={
        "position": {"type": "string", "description": "职位名称"},
        "candidate_info": {"type": "string", "description": "候选人信息"},
        "round": {"type": "integer", "description": "面试轮次"}
    }
)
def generate_interview_questions(position: str, candidate_info: str, round: int) -> Dict[str, Any]:
    round_descriptions = {
        1: "技术面试，考察核心技术能力",
        2: "综合面试，考察项目经验和团队协作",
        3: "HR面试，考察文化契合度和薪资期望"
    }
    
    prompt = PromptTemplate(
        template="""为以下面试生成问题。

职位：{position}
面试轮次：第{round}轮 - {round_desc}
候选人信息：{candidate_info}

请生成5-8个问题，涵盖该轮次的考察重点。

输出JSON格式：{{"questions": [{{"question": "...", "expected_points": [...]}}]}}""",
        input_variables=["position", "round", "round_desc", "candidate_info"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    return chain.invoke({
        "position": position,
        "round": round,
        "round_desc": round_descriptions.get(round, "综合面试"),
        "candidate_info": candidate_info
    })


@register_tool(
    name="calculate_match_score",
    description="计算候选人匹配度",
    parameters={
        "candidate_skills": {"type": "string", "description": "候选人技能"},
        "position_requirements": {"type": "string", "description": "职位要求"}
    }
)
def calculate_match_score(candidate_skills: str, position_requirements: str) -> Dict[str, Any]:
    prompt = PromptTemplate(
        template="""计算候选人与职位的匹配度。

候选人技能：
{candidate_skills}

职位要求：
{position_requirements}

请从以下维度评估：
1. 技能匹配度（0-100）
2. 经验匹配度（0-100）
3. 文化匹配度（0-100）
4. 综合匹配度

输出JSON格式：{{"skill_match": number, "experience_match": number, "culture_match": number, "overall_match": number, "explanation": "..."}}""",
        input_variables=["candidate_skills", "position_requirements"]
    )
    
    chain = prompt | llm | JsonOutputParser()
    return chain.invoke({
        "candidate_skills": candidate_skills,
        "position_requirements": position_requirements
    })
