"""岗位路由：招聘需求的增删改查。

岗位与 JD 分两层：岗位承载"要招什么人"（名称、部门、地点、编制），
JD 承载"具体要求"，挂在岗位下面。一个岗位可以先建后补 JD；
没有已启用的 JD 时，该岗位下的候选人无法启动 AI 人岗匹配。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.models.database import get_db, Position, JobDescription, Candidate
from src.models.schemas import PositionCreate, PositionUpdate, PositionResponse
from src.api.auth import get_current_user, require_hr_admin

router = APIRouter(prefix="/positions", tags=["positions"])


def _to_response(p: Position, db: Session) -> PositionResponse:
    jds = db.query(JobDescription).filter(JobDescription.position_id == p.id).all()
    data = PositionResponse.model_validate(p)
    data.jd_count = len(jds)
    data.active_jd_count = len([j for j in jds if j.status == "active"])
    # 候选人按绑定的 JD 统计，因此要经 JD 间接汇总
    jd_ids = [j.id for j in jds]
    data.candidate_count = (
        db.query(Candidate).filter(Candidate.job_description_id.in_(jd_ids)).count()
        if jd_ids else 0
    )
    return data


@router.get("/", response_model=List[PositionResponse])
def list_positions(status: Optional[str] = None, db: Session = Depends(get_db),
                   current_user=Depends(require_hr_admin)):
    query = db.query(Position)
    if status:
        query = query.filter(Position.status == status)
    return [_to_response(p, db) for p in query.order_by(Position.id.desc()).all()]


@router.get("/{position_id}", response_model=PositionResponse)
def get_position(position_id: int, db: Session = Depends(get_db),
                 current_user=Depends(require_hr_admin)):
    p = db.query(Position).filter(Position.id == position_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="岗位不存在")
    return _to_response(p, db)


@router.post("/", response_model=PositionResponse)
def create_position(body: PositionCreate, db: Session = Depends(get_db),
                    current_user=Depends(require_hr_admin)):
    p = Position(
        title=body.title, department=body.department, location=body.location,
        headcount=body.headcount, description=body.description,
        status="draft", created_by=current_user.id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_response(p, db)


@router.put("/{position_id}", response_model=PositionResponse)
def update_position(position_id: int, body: PositionUpdate, db: Session = Depends(get_db),
                    current_user=Depends(require_hr_admin)):
    p = db.query(Position).filter(Position.id == position_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="岗位不存在")
    for field in ("title", "department", "location", "headcount", "description", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(p, field, value)
    db.commit()
    db.refresh(p)
    return _to_response(p, db)


@router.post("/{position_id}/close", response_model=PositionResponse)
def close_position(position_id: int, db: Session = Depends(get_db),
                   current_user=Depends(require_hr_admin)):
    """关闭招聘：岗位与其下 JD 一起停用，但保留数据可追溯"""
    p = db.query(Position).filter(Position.id == position_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="岗位不存在")
    p.status = "archived"
    jds = db.query(JobDescription).filter(JobDescription.position_id == p.id).all()
    for jd in jds:
        if jd.status == "active":
            jd.status = "archived"
    db.commit()
    db.refresh(p)
    return _to_response(p, db)


@router.delete("/{position_id}")
def delete_position(position_id: int, db: Session = Depends(get_db),
                    current_user=Depends(require_hr_admin)):
    """删除岗位。已有 JD 或候选人关联时拒绝，引导先删 JD 或改为「关闭招聘」"""
    p = db.query(Position).filter(Position.id == position_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="岗位不存在")
    jd_count = db.query(JobDescription).filter(JobDescription.position_id == p.id).count()
    if jd_count:
        raise HTTPException(status_code=400,
                            detail=f"该岗位下还有 {jd_count} 份 JD，请先删除或改用「关闭招聘」")
    db.delete(p)
    db.commit()
    return {"message": "岗位已删除"}