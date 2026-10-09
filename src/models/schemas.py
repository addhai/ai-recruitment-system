from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime

from src.safety import OutputGuard

# 密码最短长度。种子口令 admin123 就是 8 位——这是下限而不是推荐值，
# 真正的强度要求应由部署方按自身安全基线抬高。
MIN_PASSWORD_LENGTH = 8


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    department: Optional[str] = None
    role: str = "viewer"


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    department: Optional[str]
    role: str
    is_active: bool = True
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    """管理员修改用户。全部可选，只传要改的项。

    用户名不可改：它是 JWT 的 sub、也是各表的关联依据，改名会让已签发的令牌
    与历史记录对不上。要换用户名请新建账号并停用旧的。
    """
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class AdminPasswordReset(BaseModel):
    """管理员为用户重置密码（用户忘了密码时的唯一出路）"""
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    """改密请求。

    校验放在 schema 层：非法输入应在进入业务逻辑前就被挡掉，
    而不是先查库、比对旧密码，最后才说"新密码太短"。
    """
    old_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CandidateCreate(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    position: Optional[str] = None
    # 绑定的岗位 JD；人岗匹配强制依赖它，未绑定时不允许启动 AI 评估
    job_description_id: Optional[int] = None


class CandidateUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    position: Optional[str] = None
    job_description_id: Optional[int] = None


class CandidateResponse(BaseModel):
    id: int
    name: str
    email: Optional[str]
    phone: Optional[str]
    resume_file: Optional[str]
    status: str
    source: Optional[str]
    position: Optional[str]
    job_description_id: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------- 岗位

class PositionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    location: Optional[str] = Field(None, max_length=100)
    headcount: Optional[int] = None
    description: Optional[str] = None


class PositionUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    location: Optional[str] = Field(None, max_length=100)
    headcount: Optional[int] = None
    description: Optional[str] = None
    status: Optional[str] = None


class PositionResponse(BaseModel):
    id: int
    title: str
    department: Optional[str]
    location: Optional[str]
    headcount: Optional[int]
    description: Optional[str]
    status: str
    jd_count: int = 0
    active_jd_count: int = 0
    candidate_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------- 岗位 JD

class JobDescriptionCreate(BaseModel):
    position_id: int
    title: str = Field(..., min_length=1, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    raw_text: Optional[str] = None


class JobDescriptionUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    raw_text: Optional[str] = None
    # 人工核对后修正的结构化画像
    parsed_data: Optional[Dict[str, Any]] = None


class JobDescriptionResponse(BaseModel):
    id: int
    position_id: Optional[int]
    title: str
    department: Optional[str]
    status: str
    parse_status: str
    parse_error: Optional[str]
    raw_text: Optional[str]
    parsed_data: Optional[Dict[str, Any]]
    candidate_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------- 人工复核

class ReviewDecision(BaseModel):
    """人工复核裁决：不重跑 LLM，直接改写终局状态。

    重跑会引入二次不可复现——这正是本次改造要消除的问题。
    """
    decision: str = Field(..., pattern="^(approve|reject)$",
                          description="approve=通过录用，reject=淘汰")
    note: Optional[str] = None


class ResumeCreate(BaseModel):
    candidate_id: int
    file_name: str
    file_path: str
    parsed_data: Optional[Dict[str, Any]] = None
    skills: Optional[List[str]] = None
    experience: Optional[str] = None
    education: Optional[str] = None


class ResumeResponse(BaseModel):
    id: int
    candidate_id: int
    file_name: str
    skills: Optional[List[str]]
    experience: Optional[str]
    education: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class InterviewCreate(BaseModel):
    candidate_id: int
    position: str
    round: int = 1
    interviewer_id: Optional[int] = None
    scheduled_at: Optional[datetime] = None


class InterviewUpdate(BaseModel):
    status: Optional[str] = None
    score: Optional[int] = None
    feedback: Optional[str] = None
    notes: Optional[str] = None


class InterviewResponse(BaseModel):
    id: int
    candidate_id: int
    position: str
    round: int
    status: str
    interviewer_id: Optional[int]
    scheduled_at: Optional[datetime]
    completed_at: Optional[datetime]
    score: Optional[int]
    feedback: Optional[str]
    notes: Optional[str] = None
    questions: Optional[List[Dict[str, Any]]] = None  # AI 生成的该轮面试题
    created_at: datetime

    class Config:
        from_attributes = True

    # AI 生成内容出口：递归脱敏 PII（面试官手写 feedback/notes 属业务记录，不在此处理）
    @field_validator("questions", mode="before")
    @classmethod
    def _sanitize_questions(cls, v):
        return OutputGuard.sanitize_obj(v) if v else v


class QuestionnaireCreate(BaseModel):
    name: str
    type: str = "technical"
    questions: List[Dict[str, Any]]


class QuestionnaireResponseCreate(BaseModel):
    candidate_id: int
    # 路径参数已携带 questionnaire_id，body 中可省略（兼容仍传的客户端）
    questionnaire_id: Optional[int] = None
    responses: Dict[str, Any]


class QuestionnaireResponseResponse(BaseModel):
    id: int
    candidate_id: int
    questionnaire_id: int
    score: Optional[int]
    status: str
    submitted_at: datetime

    class Config:
        from_attributes = True


class EvaluationCreate(BaseModel):
    candidate_id: int
    dimension: str
    score: int
    comment: Optional[str] = None


class EvaluationResponse(BaseModel):
    id: int
    candidate_id: int
    # AI 工作流自动写入的评审记录没有人类评估人，允许为空
    evaluator_id: Optional[int] = None
    dimension: str
    score: int
    comment: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

    # AI 评审评语出口：脱敏 PII，防止模型在评语中复述简历手机号/身份证
    @field_validator("comment", mode="before")
    @classmethod
    def _sanitize_comment(cls, v):
        return OutputGuard.sanitize(v) if isinstance(v, str) else v


class TalentPoolCreate(BaseModel):
    candidate_id: int
    tags: Optional[List[str]] = None
    notes: Optional[str] = None


class TalentPoolResponse(BaseModel):
    id: int
    candidate_id: int
    status: str
    tags: Optional[List[str]]
    last_contact: Optional[datetime]
    next_contact: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class WorkflowRunResponse(BaseModel):
    id: int
    candidate_id: int
    status: str
    current_step: Optional[str]
    progress: int
    results: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True


class MatchScore(BaseModel):
    skill_match: float
    culture_match: float
    communication_match: float
    overall_score: float


class DashboardStats(BaseModel):
    total_candidates: int
    pending_candidates: int
    interviewed_candidates: int
    hired_candidates: int
    avg_interview_time: float
    avg_match_score: float
    this_month_candidates: int
    interview_progress: List[Dict[str, Any]]