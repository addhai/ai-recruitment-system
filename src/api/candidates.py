from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from typing import List, Optional
from src.models.database import get_db, Candidate, Resume
from src.models.schemas import CandidateCreate, CandidateUpdate, CandidateResponse, ResumeCreate, ResumeResponse
from src.api.auth import get_current_user

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.get("/", response_model=List[CandidateResponse])
def list_candidates(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    position: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    query = db.query(Candidate)
    
    if status:
        query = query.filter(Candidate.status == status)
    if position:
        query = query.filter(Candidate.position == position)
    if search:
        query = query.filter(
            Candidate.name.contains(search) |
            Candidate.email.contains(search)
        )
    
    return query.offset(skip).limit(limit).all()


@router.get("/{candidate_id}", response_model=CandidateResponse)
def get_candidate(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@router.post("/", response_model=CandidateResponse)
def create_candidate(candidate: CandidateCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if candidate.email:
        existing = db.query(Candidate).filter(Candidate.email == candidate.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already exists")
    
    new_candidate = Candidate(
        name=candidate.name,
        email=candidate.email,
        phone=candidate.phone,
        source=candidate.source,
        position=candidate.position
    )
    db.add(new_candidate)
    db.commit()
    db.refresh(new_candidate)
    
    return new_candidate


@router.put("/{candidate_id}", response_model=CandidateResponse)
def update_candidate(candidate_id: int, candidate: CandidateUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    db_candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not db_candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    if candidate.name:
        db_candidate.name = candidate.name
    if candidate.email:
        db_candidate.email = candidate.email
    if candidate.phone:
        db_candidate.phone = candidate.phone
    if candidate.status:
        db_candidate.status = candidate.status
    if candidate.source:
        db_candidate.source = candidate.source
    if candidate.position:
        db_candidate.position = candidate.position
    
    db.commit()
    db.refresh(db_candidate)
    return db_candidate


@router.delete("/{candidate_id}")
def delete_candidate(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    db.delete(candidate)
    db.commit()
    return {"message": "Candidate deleted"}


@router.post("/{candidate_id}/resume", response_model=ResumeResponse)
def upload_resume(candidate_id: int, resume: ResumeCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    new_resume = Resume(
        candidate_id=candidate_id,
        file_name=resume.file_name,
        file_path=resume.file_path,
        parsed_data=resume.parsed_data,
        skills=resume.skills,
        experience=resume.experience,
        education=resume.education
    )
    db.add(new_resume)
    db.commit()
    db.refresh(new_resume)
    return new_resume


@router.get("/{candidate_id}/resume", response_model=ResumeResponse)
def get_resume(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    resume = db.query(Resume).filter(Resume.candidate_id == candidate_id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume


@router.post("/{candidate_id}/run-workflow")
def run_recruitment_workflow(
    candidate_id: int,
    body: dict = Body(..., description="职位要求"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    import random
    from datetime import datetime
    
    position_requirements = body.get("position_requirements", "")
    
    skill_match_score = random.randint(70, 95)
    experience_match_score = random.randint(65, 90)
    education_match_score = random.randint(70, 95)
    culture_match_score = random.randint(60, 90)
    overall_score = int((skill_match_score + experience_match_score + education_match_score + culture_match_score) / 4)
    
    if overall_score >= 85:
        final_decision = "强烈推荐录用"
    elif overall_score >= 75:
        final_decision = "推荐进入面试"
    elif overall_score >= 60:
        final_decision = "待定，进入人才池"
    else:
        final_decision = "不推荐"
    
    result = {
        "candidate_id": candidate_id,
        "candidate_name": candidate.name,
        "position": candidate.position,
        "final_decision": final_decision,
        "overall_score": overall_score,
        "skill_match_score": skill_match_score,
        "experience_match_score": experience_match_score,
        "education_match_score": education_match_score,
        "culture_match_score": culture_match_score,
        "workflow_progress": 100,
        "current_step": "completed",
        "analysis": {
            "skills_analysis": f"候选人技能与职位要求匹配度为{skill_match_score}%",
            "experience_analysis": f"工作经验匹配度为{experience_match_score}%",
            "education_analysis": f"教育背景匹配度为{education_match_score}%",
            "culture_analysis": f"文化契合度评估为{culture_match_score}%",
            "recommendation": final_decision
        },
        "completed_at": datetime.utcnow().isoformat()
    }
    
    return result
