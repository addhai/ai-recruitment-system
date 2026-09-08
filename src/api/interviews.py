from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from src.models.database import get_db, Interview
from src.models.schemas import InterviewCreate, InterviewUpdate, InterviewResponse
from src.api.auth import get_current_user
from src.sse.notification import (
    notify_interview_scheduled as notify_sse_interview_scheduled,
    notify_interview_completed as notify_sse_interview_completed,
)
from src.safety import InputGuard

router = APIRouter(prefix="/interviews", tags=["interviews"])


async def _resume_workflow_after_interview(
    candidate_id: int, interview_id: int, round_no: int,
    score: int, feedback: str, notes: Optional[str],
):
    """面试结果录入后自动恢复挂起的招聘工作流（失败不影响录入结果）"""
    try:
        from src.workflow.runner import resume_workflow
        await resume_workflow(candidate_id, "await_interview", {
            "interview_id": interview_id,
            "round": round_no,
            "score": score,
            "feedback": feedback,
            "notes": notes,
        })
    except Exception as e:
        print(f"[interviews] 面试完成后自动恢复工作流失败: {e}")


@router.get("/", response_model=List[InterviewResponse])
def list_interviews(
    skip: int = 0,
    limit: int = 100,
    candidate_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    query = db.query(Interview)
    
    if candidate_id:
        query = query.filter(Interview.candidate_id == candidate_id)
    if status:
        query = query.filter(Interview.status == status)
    
    return query.offset(skip).limit(limit).all()


@router.get("/{interview_id}", response_model=InterviewResponse)
def get_interview(interview_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    return interview


@router.post("/", response_model=InterviewResponse)
async def create_interview(interview: InterviewCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    new_interview = Interview(
        candidate_id=interview.candidate_id,
        position=interview.position,
        round=interview.round,
        interviewer_id=interview.interviewer_id,
        scheduled_at=interview.scheduled_at
    )
    db.add(new_interview)
    db.commit()
    db.refresh(new_interview)

    scheduled_time_str = interview.scheduled_at.strftime("%Y-%m-%d %H:%M") if interview.scheduled_at else "待定"

    # SSE 通知前端面试已安排
    try:
        await notify_sse_interview_scheduled(
            interview.candidate_id,
            new_interview.id,
            interview.round,
            scheduled_time_str,
        )
    except Exception:
        pass

    return new_interview


@router.put("/{interview_id}", response_model=InterviewResponse)
def update_interview(interview_id: int, interview: InterviewUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    db_interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not db_interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    
    if interview.status:
        db_interview.status = interview.status
    if interview.score:
        db_interview.score = interview.score
    if interview.feedback:
        db_interview.feedback = interview.feedback
    if interview.notes:
        db_interview.notes = interview.notes
    
    if interview.status == "completed" and not db_interview.completed_at:
        db_interview.completed_at = datetime.utcnow()
    
    db.commit()
    db.refresh(db_interview)
    return db_interview


@router.delete("/{interview_id}")
def delete_interview(interview_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    db.delete(interview)
    db.commit()
    return {"message": "Interview deleted"}


@router.post("/{interview_id}/complete")
async def complete_interview(
    interview_id: int,
    background_tasks: BackgroundTasks,
    score: int,
    feedback: str,
    notes: str = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """录入面试结果。保存后自动恢复招聘工作流，由 AI 判定晋级/淘汰并推进下一环节"""
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # 反馈会进入 AI 评估节点，拦截提示注入
    is_safe, reason = InputGuard.check(f"{feedback or ''}\n{notes or ''}")
    if not is_safe:
        raise HTTPException(status_code=400, detail=f"面试反馈未通过安全检查：{reason}")

    interview.status = "completed"
    interview.score = score
    interview.feedback = feedback
    if notes:
        interview.notes = notes
    interview.completed_at = datetime.utcnow()

    db.commit()
    db.refresh(interview)

    # SSE 通知面试完成
    try:
        await notify_sse_interview_completed(interview.candidate_id, interview_id, score, feedback)
    except Exception:
        pass

    # 自动恢复工作流：AI 判定该轮是否通过，通过则自动排下一轮/进入综合评审
    background_tasks.add_task(
        _resume_workflow_after_interview,
        interview.candidate_id, interview_id, interview.round, score, feedback, notes,
    )
    return interview
