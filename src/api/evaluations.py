from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from src.models.database import get_db, Evaluation
from src.models.schemas import EvaluationCreate, EvaluationResponse
from src.api.auth import get_current_user, require_hr_admin, require_hr_admin_interviewer
from src.api.pagination import SkipParam, LimitParam
from src.evaluation import evaluation_tracker

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.get("/stats")
def get_workflow_evaluation_stats(current_user=Depends(require_hr_admin_interviewer)):
    """获取 AI 工作流评估统计摘要（基于内存追踪器）"""
    return evaluation_tracker.stats()


@router.get("/stats/records")
def get_workflow_evaluation_records(
    limit: int = 20,
    current_user=Depends(require_hr_admin_interviewer)
):
    """获取最近的 AI 工作流评估记录"""
    return evaluation_tracker.get_records(limit=limit)


@router.get("/", response_model=List[EvaluationResponse])
def list_evaluations(
    candidate_id: int = None,
    dimension: str = None,
    skip: SkipParam = 0,
    limit: LimitParam = 100,
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin_interviewer)
):
    query = db.query(Evaluation)

    if candidate_id:
        query = query.filter(Evaluation.candidate_id == candidate_id)
    if dimension:
        query = query.filter(Evaluation.dimension == dimension)

    return query.offset(skip).limit(limit).all()


@router.get("/{evaluation_id}", response_model=EvaluationResponse)
def get_evaluation(evaluation_id: int, db: Session = Depends(get_db), current_user=Depends(require_hr_admin_interviewer)):
    evaluation = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return evaluation


@router.post("/", response_model=EvaluationResponse)
def create_evaluation(evaluation: EvaluationCreate, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    new_evaluation = Evaluation(
        candidate_id=evaluation.candidate_id,
        evaluator_id=current_user.id,
        dimension=evaluation.dimension,
        score=evaluation.score,
        comment=evaluation.comment
    )
    db.add(new_evaluation)
    db.commit()
    db.refresh(new_evaluation)
    return new_evaluation


@router.put("/{evaluation_id}", response_model=EvaluationResponse)
def update_evaluation(evaluation_id: int, score: int = None, comment: str = None, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    e = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    
    if score is not None:
        e.score = score
    if comment:
        e.comment = comment
    
    db.commit()
    db.refresh(e)
    return e


@router.delete("/{evaluation_id}")
def delete_evaluation(evaluation_id: int, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    e = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    db.delete(e)
    db.commit()
    return {"message": "Evaluation deleted"}


@router.get("/candidate/{candidate_id}/summary")
def get_candidate_evaluation_summary(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(require_hr_admin_interviewer)):
    evaluations = db.query(Evaluation).filter(Evaluation.candidate_id == candidate_id).all()
    
    if not evaluations:
        return {"candidate_id": candidate_id, "total_evaluations": 0, "average_score": 0, "dimensions": {}}
    
    dimension_scores = {}
    total_score = 0
    
    for e in evaluations:
        if e.dimension not in dimension_scores:
            dimension_scores[e.dimension] = []
        dimension_scores[e.dimension].append(e.score)
        total_score += e.score
    
    summary = {}
    for dim, scores in dimension_scores.items():
        summary[dim] = {
            "count": len(scores),
            "average": sum(scores) / len(scores),
            "min": min(scores),
            "max": max(scores)
        }
    
    return {
        "candidate_id": candidate_id,
        "total_evaluations": len(evaluations),
        "average_score": total_score / len(evaluations),
        "dimensions": summary
    }
