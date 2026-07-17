from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from src.models.database import get_db, Interview
from src.models.schemas import InterviewCreate, InterviewUpdate, InterviewResponse
from src.api.auth import get_current_user

router = APIRouter(prefix="/interviews", tags=["interviews"])


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
def create_interview(interview: InterviewCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
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
def complete_interview(interview_id: int, score: int, feedback: str, notes: str = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    
    interview.status = "completed"
    interview.score = score
    interview.feedback = feedback
    interview.notes = notes
    interview.completed_at = datetime.utcnow()
    
    db.commit()
    db.refresh(interview)
    return interview
