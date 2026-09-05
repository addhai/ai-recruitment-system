from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime

from src.safety import OutputGuard


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
    created_at: datetime

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CandidateCreate(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    position: Optional[str] = None


class CandidateUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    position: Optional[str] = None


class CandidateResponse(BaseModel):
    id: int
    name: str
    email: Optional[str]
    phone: Optional[str]
    resume_file: Optional[str]
    status: str
    source: Optional[str]
    position: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


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