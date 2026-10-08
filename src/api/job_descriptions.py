"""岗位 JD 路由：录入 → AI 解析 → 人工核对 → 启用 → 供人岗匹配引用。

设计要点：JD 解析错了会静默污染该岗位下所有候选人的评分，
因此强制两段式——必须先 parse 成功、且画像里带原文依据的技能项，
HR 核对后才能 activate。未达标的 JD 不可启用。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from src.models.database import get_db, JobDescription, Candidate, Position
from src.models.schemas import (
    JobDescriptionUpdate, JobDescriptionResponse,
)
from src.api.auth import get_current_user, require_hr_admin
from src.services.job_parser import (
    parse_job_description, extract_text_from_file, is_activatable, normalize_profile,
)
from src.services.resume_cleaner import validate_resume_file

router = APIRouter(prefix="/job_descriptions", tags=["job-descriptions"])


def _to_response(jd: JobDescription, db: Session) -> JobDescriptionResponse:
    count = db.query(Candidate).filter(Candidate.job_description_id == jd.id).count()
    data = JobDescriptionResponse.model_validate(jd)
    data.candidate_count = count
    return data


@router.get("/", response_model=List[JobDescriptionResponse])
def list_job_descriptions(
    status: Optional[str] = None,
    position_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin),
):
    query = db.query(JobDescription)
    if status:
        query = query.filter(JobDescription.status == status)
    if position_id:
        query = query.filter(JobDescription.position_id == position_id)
    return [_to_response(jd, db) for jd in query.order_by(JobDescription.id.desc()).all()]


@router.get("/{jd_id}", response_model=JobDescriptionResponse)
def get_job_description(jd_id: int, db: Session = Depends(get_db),
                        current_user=Depends(require_hr_admin)):
    jd = db.query(JobDescription).filter(JobDescription.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="岗位 JD 不存在")
    return _to_response(jd, db)


@router.post("/", response_model=JobDescriptionResponse)
async def create_job_description(
    position_id: int = Form(...),
    title: str = Form(...),
    department: Optional[str] = Form(None),
    raw_text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin),
):
    """在指定岗位下新建 JD。支持直接粘贴文本，或上传 txt/pdf/docx/图片（走 OCR）。"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(status_code=404, detail="岗位不存在")
    if position.status == "archived":
        raise HTTPException(status_code=400, detail="岗位已关闭，无法新增 JD")

    if file is not None:
        raw_bytes = await file.read()
        is_valid, reason, _ext = validate_resume_file(file.filename or "", len(raw_bytes))
        if not is_valid:
            raise HTTPException(status_code=400, detail=reason)
        try:
            text, _source_type, _ocr = extract_text_from_file(file.filename, raw_bytes)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"JD 文件解析失败：{str(e)[:200]}")
        if not text or len(text.strip()) < 20:
            raise HTTPException(status_code=400, detail="JD 文件内容提取失败或内容过少")
        raw_text = text

    if not raw_text or not raw_text.strip():
        raise HTTPException(status_code=400, detail="请提供 JD 文本或上传 JD 文件")

    jd = JobDescription(
        position_id=position_id,
        title=title, department=department, raw_text=raw_text,
        status="draft", parse_status="pending", created_by=current_user.id,
    )
    db.add(jd)
    db.commit()
    db.refresh(jd)
    return _to_response(jd, db)


@router.post("/{jd_id}/parse", response_model=JobDescriptionResponse)
async def parse_jd(jd_id: int, db: Session = Depends(get_db),
                   current_user=Depends(require_hr_admin)):
    """AI 解析 JD 原文为结构化岗位画像。"""
    jd = db.query(JobDescription).filter(JobDescription.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="岗位 JD 不存在")

    parsed, error = await parse_job_description(jd.raw_text or "")
    if parsed is None:
        jd.parse_status = "failed"
        jd.parse_error = error
        db.commit()
        db.refresh(jd)
        raise HTTPException(status_code=400, detail=error)

    jd.parsed_data = parsed
    jd.parse_status = "parsed"
    jd.parse_error = None
    db.commit()
    db.refresh(jd)
    return _to_response(jd, db)


@router.put("/{jd_id}", response_model=JobDescriptionResponse)
def update_job_description(jd_id: int, body: JobDescriptionUpdate,
                           db: Session = Depends(get_db),
                           current_user=Depends(require_hr_admin)):
    """人工核对/编辑 JD。改动画像后需重新核对才能启用。"""
    jd = db.query(JobDescription).filter(JobDescription.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="岗位 JD 不存在")

    if body.title is not None:
        jd.title = body.title
    if body.department is not None:
        jd.department = body.department
    if body.raw_text is not None:
        jd.raw_text = body.raw_text
        # 原文改了，旧画像即失效，必须重新解析
        jd.parse_status = "pending"
        jd.parse_error = None
        jd.parsed_data = None
        if jd.status == "active":
            jd.status = "draft"
    if body.parsed_data is not None:
        # 人工编辑同样要走归一化：没有原文依据的技能项会被丢弃，
        # 否则 HR 在编辑框里就能塞进无法核对的技能，绕过"必须可核对"这条纪律
        profile = normalize_profile(body.parsed_data)
        if profile is None:
            raise HTTPException(
                status_code=400,
                detail="岗位画像中没有带原文依据的技能项，无法保存",
            )
        jd.parsed_data = profile
        jd.parse_status = "parsed"
        jd.parse_error = None

    db.commit()
    db.refresh(jd)
    return _to_response(jd, db)


@router.post("/{jd_id}/activate", response_model=JobDescriptionResponse)
def activate_job_description(jd_id: int, db: Session = Depends(get_db),
                             current_user=Depends(require_hr_admin)):
    """启用 JD：必须已解析成功且含带原文依据的技能项。"""
    jd = db.query(JobDescription).filter(JobDescription.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="岗位 JD 不存在")
    if jd.parse_status != "parsed":
        raise HTTPException(status_code=400, detail="JD 尚未解析成功，无法启用")
    ok, reason = is_activatable(jd.parsed_data)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    jd.status = "active"
    db.commit()
    db.refresh(jd)
    return _to_response(jd, db)


@router.post("/{jd_id}/archive", response_model=JobDescriptionResponse)
def archive_job_description(jd_id: int, db: Session = Depends(get_db),
                            current_user=Depends(require_hr_admin)):
    """停用 JD：已绑定的候选人保留绑定，但不能再启动新的匹配。"""
    jd = db.query(JobDescription).filter(JobDescription.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="岗位 JD 不存在")
    jd.status = "archived"
    db.commit()
    db.refresh(jd)
    return _to_response(jd, db)


@router.delete("/{jd_id}")
def delete_job_description(jd_id: int, db: Session = Depends(get_db),
                           current_user=Depends(require_hr_admin)):
    """删除 JD。已有候选人绑定时拒绝，改为引导停用。"""
    jd = db.query(JobDescription).filter(JobDescription.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="岗位 JD 不存在")
    count = db.query(Candidate).filter(Candidate.job_description_id == jd_id).count()
    if count:
        raise HTTPException(
            status_code=400,
            detail=f"该岗位已有 {count} 位候选人绑定，无法删除；请改用「停用」",
        )
    db.delete(jd)
    db.commit()
    return {"message": "岗位 JD 已删除"}