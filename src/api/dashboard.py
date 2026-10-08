from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from src.models.database import get_db, Candidate, Interview, Evaluation
from src.api.auth import get_current_user, require_all_authenticated
from src.models.schemas import DashboardStats

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
def get_dashboard_stats(db: Session = Depends(get_db), current_user=Depends(require_all_authenticated)):
    total_candidates = db.query(Candidate).count()
    
    pending_candidates = db.query(Candidate).filter(Candidate.status == "pending").count()
    
    interviewed_candidates = db.query(Candidate).filter(Candidate.status == "interviewed").count()
    
    hired_candidates = db.query(Candidate).filter(Candidate.status == "hired").count()
    
    completed_interviews = db.query(Interview).filter(Interview.status == "completed").all()
    # 平均面试时长 = 排期到完成的分钟数。
    # 工作流自动排期落在未来，HR 提前录入结果时 completed_at 会早于 scheduled_at，
    # 直接参与计算会让均值变成负数；这类样本必须剔除，
    # 且分母要与实际参与统计的样本数保持一致，否则均值被稀释。
    durations = [
        (i.completed_at - i.scheduled_at).total_seconds() / 60
        for i in completed_interviews
        if i.scheduled_at and i.completed_at and i.completed_at >= i.scheduled_at
    ]
    avg_interview_time = sum(durations) / len(durations) if durations else 0
    
    evaluations = db.query(Evaluation).all()
    avg_match_score = 0
    if evaluations:
        total_score = sum(e.score for e in evaluations)
        avg_match_score = total_score / len(evaluations)
    
    this_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_month_candidates = db.query(Candidate).filter(Candidate.created_at >= this_month).count()
    
    interview_progress = []
    status_counts = db.query(Interview.status, func.count(Interview.id)).group_by(Interview.status).all()
    for status, count in status_counts:
        interview_progress.append({"status": status, "count": count})
    
    return {
        "total_candidates": total_candidates,
        "pending_candidates": pending_candidates,
        "interviewed_candidates": interviewed_candidates,
        "hired_candidates": hired_candidates,
        "avg_interview_time": avg_interview_time,
        "avg_match_score": avg_match_score,
        "this_month_candidates": this_month_candidates,
        "interview_progress": interview_progress
    }


@router.get("/recent-candidates")
def get_recent_candidates(limit: int = 10, db: Session = Depends(get_db), current_user=Depends(require_all_authenticated)):
    candidates = db.query(Candidate).order_by(Candidate.created_at.desc()).limit(limit).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "position": c.position,
            "status": c.status,
            "created_at": c.created_at.isoformat()
        }
        for c in candidates
    ]


@router.get("/interview-stats")
def get_interview_stats(db: Session = Depends(get_db), current_user=Depends(require_all_authenticated)):
    total_interviews = db.query(Interview).count()
    completed = db.query(Interview).filter(Interview.status == "completed").count()
    scheduled = db.query(Interview).filter(Interview.status == "scheduled").count()
    in_progress = db.query(Interview).filter(Interview.status == "in_progress").count()
    
    avg_score = 0
    scored_interviews = db.query(Interview).filter(Interview.score.isnot(None)).all()
    if scored_interviews:
        avg_score = sum(i.score for i in scored_interviews) / len(scored_interviews)
    
    return {
        "total_interviews": total_interviews,
        "completed": completed,
        "scheduled": scheduled,
        "in_progress": in_progress,
        "avg_score": avg_score,
        "completion_rate": (completed / total_interviews) * 100 if total_interviews > 0 else 0
    }


@router.get("/weekly-trend")
def get_weekly_trend(db: Session = Depends(get_db), current_user=Depends(require_all_authenticated)):
    today = datetime.now()
    trend = []
    
    for i in range(6, -1, -1):
        date = today - timedelta(days=i)
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        candidates_count = db.query(Candidate).filter(
            Candidate.created_at >= start_of_day,
            Candidate.created_at <= end_of_day
        ).count()
        
        interviews_count = db.query(Interview).filter(
            Interview.created_at >= start_of_day,
            Interview.created_at <= end_of_day
        ).count()
        
        trend.append({
            "date": date.strftime("%Y-%m-%d"),
            "day": date.strftime("%a"),
            "candidates": candidates_count,
            "interviews": interviews_count
        })
    
    return trend
