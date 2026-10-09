"""人工复核路由（标记式复核的闭环）。

文化契合落在待复核区间时，工作流不阻塞、继续跑完，终局锁档为「待人工复核」
并落到 status=pending_review。HR 在这里事后批量裁决。

关键约束：裁决**不重跑 LLM**，直接改写终局状态。重跑会引入二次不可复现，
而消除不可复现正是本次改造的目的。
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.models.database import get_db, Candidate, Evaluation, TalentPool, WorkflowRun
from src.models.schemas import ReviewDecision
from src.api.auth import get_current_user, require_hr_admin
from src.api.pagination import SkipParam, LimitParam

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _upsert_pool_tags(db: Session, candidate_id: int, tags: List[str], notes: str) -> None:
    """改写人才池标签（幂等：已存在则替换标签并更新备注）"""
    pool = db.query(TalentPool).filter(TalentPool.candidate_id == candidate_id).first()
    if pool:
        pool.tags = tags
        pool.notes = notes
    else:
        db.add(TalentPool(candidate_id=candidate_id, status="active", tags=tags, notes=notes))
    db.commit()


@router.get("/")
def list_pending_reviews(skip: SkipParam = 0, limit: LimitParam = 100,
                         db: Session = Depends(get_db),
                         current_user=Depends(require_hr_admin)):
    """待复核候选人列表：带维度分数快照与终局结果，供 HR 批量裁决。"""
    candidates = (
        db.query(Candidate)
        .filter(Candidate.status == "pending_review")
        .order_by(Candidate.updated_at.desc())
        .offset(skip).limit(limit)
        .all()
    )
    items = []
    for c in candidates:
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.candidate_id == c.id)
            .order_by(WorkflowRun.id.desc())
            .first()
        )
        results = (run.results or {}) if run else {}
        pool = db.query(TalentPool).filter(TalentPool.candidate_id == c.id).first()
        items.append({
            "candidate_id": c.id,
            "name": c.name,
            "position": c.position,
            "job_description_id": c.job_description_id,
            "status": c.status,
            "final_decision": results.get("final_decision"),
            "overall_score": results.get("overall_score"),
            "skill_match_score": results.get("skill_match_score"),
            "experience_match_score": results.get("experience_match_score"),
            "education_match_score": results.get("education_match_score"),
            "culture_match_score": results.get("culture_match_score"),
            "questionnaire_score": results.get("questionnaire_score"),
            "interview_scores": results.get("interview_scores") or [],
            "needs_review": results.get("needs_review"),
            "review_reason": results.get("review_reason"),
            "review_detail": results.get("review_detail"),
            "assessed_dimensions": results.get("assessed_dimensions") or [],
            "scoring_version": results.get("scoring_version"),
            "talent_pool_tags": (pool.tags if pool else None),
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        })
    return items


@router.post("/candidates/{candidate_id}", tags=["reviews"])
def decide_review(candidate_id: int, body: ReviewDecision,
                  db: Session = Depends(get_db),
                  current_user=Depends(require_hr_admin)):
    """裁决一位待复核候选人：approve=通过录用，reject=淘汰。

    不重跑工作流：直接改写候选人状态与人才池标签，并留一条人工复核评估记录。
    """
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="候选人不存在")
    if c.status != "pending_review":
        raise HTTPException(status_code=400,
                            detail=f"该候选人当前状态为 {c.status}，不在待复核队列中")

    note = (body.note or "").strip()
    stamp = datetime.utcnow().isoformat()
    if body.decision == "approve":
        c.status = "hired"
        tags = ["已录用", "人工复核通过"]
        notes = f"人工复核通过（{stamp}，裁决人 {current_user.username}）" + (f"：{note}" if note else "")
        dim_score = 100
    else:
        c.status = "rejected"
        tags = ["已淘汰", "人工复核淘汰"]
        notes = f"人工复核淘汰（{stamp}，裁决人 {current_user.username}）" + (f"：{note}" if note else "")
        dim_score = 0
    c.updated_at = datetime.utcnow()
    db.commit()

    _upsert_pool_tags(db, c.id, tags, notes)

    db.add(Evaluation(
        candidate_id=c.id,
        evaluator_id=current_user.id,
        dimension="人工复核",
        score=dim_score,
        comment=notes[:300],
    ))
    db.commit()

    return {"candidate_id": c.id, "status": c.status, "tags": tags, "note": notes}