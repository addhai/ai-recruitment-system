from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
from src.models.database import get_db, TalentPool, Candidate
from src.models.schemas import TalentPoolCreate, TalentPoolResponse
from src.api.auth import get_current_user

router = APIRouter(prefix="/talent-pool", tags=["talent-pool"])


@router.get("/", response_model=List[TalentPoolResponse])
def list_talent_pool(
    status: str = None,
    tag: str = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    query = db.query(TalentPool)
    
    if status:
        query = query.filter(TalentPool.status == status)
    if tag:
        query = query.filter(TalentPool.tags.any(tag))
    
    return query.all()


@router.get("/{pool_id}", response_model=TalentPoolResponse)
def get_talent_pool(pool_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    pool = db.query(TalentPool).filter(TalentPool.id == pool_id).first()
    if not pool:
        raise HTTPException(status_code=404, detail="Talent pool entry not found")
    return pool


@router.post("/", response_model=TalentPoolResponse)
def add_to_pool(pool: TalentPoolCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    existing = db.query(TalentPool).filter(TalentPool.candidate_id == pool.candidate_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Candidate already in talent pool")
    
    candidate = db.query(Candidate).filter(Candidate.id == pool.candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    new_pool = TalentPool(
        candidate_id=pool.candidate_id,
        tags=pool.tags or [],
        notes=pool.notes
    )
    db.add(new_pool)
    db.commit()
    db.refresh(new_pool)
    return new_pool


@router.put("/{pool_id}", response_model=TalentPoolResponse)
def update_pool(pool_id: int, status: str = None, tags: List[str] = None, notes: str = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    pool = db.query(TalentPool).filter(TalentPool.id == pool_id).first()
    if not pool:
        raise HTTPException(status_code=404, detail="Talent pool entry not found")
    
    if status:
        pool.status = status
    if tags is not None:
        pool.tags = tags
    if notes:
        pool.notes = notes
    
    db.commit()
    db.refresh(pool)
    return pool


@router.delete("/{pool_id}")
def remove_from_pool(pool_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    pool = db.query(TalentPool).filter(TalentPool.id == pool_id).first()
    if not pool:
        raise HTTPException(status_code=404, detail="Talent pool entry not found")
    db.delete(pool)
    db.commit()
    return {"message": "Candidate removed from talent pool"}


@router.post("/{pool_id}/contact")
def record_contact(pool_id: int, notes: str = None, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    pool = db.query(TalentPool).filter(TalentPool.id == pool_id).first()
    if not pool:
        raise HTTPException(status_code=404, detail="Talent pool entry not found")
    
    pool.last_contact = datetime.utcnow()
    if notes:
        pool.notes = notes
    
    db.commit()
    db.refresh(pool)
    return pool
