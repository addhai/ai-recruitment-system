import os
import tempfile
from fastapi import APIRouter, Depends, HTTPException, Query, Body, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from src.models.database import get_db, Candidate, Resume, WorkflowRun
from src.models.schemas import CandidateCreate, CandidateUpdate, CandidateResponse, ResumeCreate, ResumeResponse, WorkflowRunResponse
from src.api.pagination import SkipParam, LimitParam
from src.api.auth import get_current_user, require_hr_admin, require_hr_admin_interviewer
from src.safety import InputGuard, OutputGuard
from src.sse.notification import notify_candidate_added
from src.workflow import db_actions
from src.workflow.runner import start_workflow, resume_workflow, trigger_after_upload
from src.services.resume_cleaner import (
    validate_resume_file,
    parse_resume_with_fallback,
    segment_text,
)


def _sanitize_output(obj):
    """递归过滤输出对象中的 PII（实现统一收敛在 OutputGuard.sanitize_obj）"""
    return OutputGuard.sanitize_obj(obj)

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.get("/", response_model=List[CandidateResponse])
def list_candidates(
    skip: SkipParam = 0,
    limit: LimitParam = 100,
    status: Optional[str] = None,
    position: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin_interviewer)
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
def get_candidate(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(require_hr_admin_interviewer)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@router.post("/", response_model=CandidateResponse)
async def create_candidate(candidate: CandidateCreate, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    if candidate.email:
        existing = db.query(Candidate).filter(Candidate.email == candidate.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already exists")
    
    new_candidate = Candidate(
        name=candidate.name,
        email=candidate.email,
        phone=candidate.phone,
        source=candidate.source,
        position=candidate.position,
        job_description_id=candidate.job_description_id
    )
    db.add(new_candidate)
    db.commit()
    db.refresh(new_candidate)

    # 通过 SSE 通知前端有新候选人加入
    try:
        await notify_candidate_added(new_candidate.id, new_candidate.name)
    except Exception:
        pass
    
    return new_candidate


@router.put("/{candidate_id}", response_model=CandidateResponse)
def update_candidate(candidate_id: int, candidate: CandidateUpdate, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
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
    if candidate.job_description_id is not None:
        db_candidate.job_description_id = candidate.job_description_id
    
    db.commit()
    db.refresh(db_candidate)
    return db_candidate


@router.delete("/{candidate_id}")
async def delete_candidate(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    # 先清 checkpoint：级联删除会移除 WorkflowRun 行，事后就取不到 thread_id 了
    from src.workflow.runner import purge_candidate_workflow_data
    purged = await purge_candidate_workflow_data(candidate_id)
    db.delete(candidate)  # ORM 级联删除关联业务行
    db.commit()
    return {"message": "Candidate deleted", "purged_checkpoint_threads": purged}


@router.post("/{candidate_id}/resume", response_model=ResumeResponse)
def upload_resume(candidate_id: int, resume: ResumeCreate, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
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
def get_resume(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(require_hr_admin_interviewer)):
    resume = db.query(Resume).filter(Resume.candidate_id == candidate_id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume


@router.post("/{candidate_id}/upload-resume")
async def upload_resume_file(
    candidate_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin)
):
    """上传简历文件，提取文本并存入 Resume 表

    支持格式: .txt .pdf .docx .doc .html .htm
    数据清洗流程: 格式校验 → 编码统一 → 文本提取 → 空白清理 → 特殊字符过滤 → 安全检查 → LLM解析
    扫描件PDF支持OCR兜底（需安装 pdf2image + pytesseract）
    """
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # 1. 读取文件内容
    raw_bytes = await file.read()
    file_size = len(raw_bytes)
    filename = file.filename or "resume.txt"

    # 2. 格式校验（文件类型 + 大小）
    is_valid, reason, ext = validate_resume_file(filename, file_size)
    if not is_valid:
        raise HTTPException(status_code=400, detail=reason)

    # 3. 保存到临时文件（PDF/DOCX需要文件路径）
    suffix = ext or os.path.splitext(filename)[1]
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(tmp_fd, "wb") as tmp:
            tmp.write(raw_bytes)

        # 4. 文本提取 + 数据清洗（编码统一、空白清理、特殊字符过滤、OCR兜底）
        parsed_text, source_type, used_ocr = parse_resume_with_fallback(
            tmp_path, filename, raw_bytes
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not parsed_text or len(parsed_text) < 10:
        raise HTTPException(
            status_code=400,
            detail=f"简历内容提取失败或内容过少(来源:{source_type})，请确认文件内容或使用其他格式"
        )

    # 5. 安全检查：在送入 LLM 解析前对简历原文做输入防护
    is_safe, reason = InputGuard.check(parsed_text)
    if not is_safe:
        raise HTTPException(status_code=400, detail=f"简历内容未通过安全检查：{reason}")

    # 6. 长文本分段（超过3000字符按段落分割，取首段做结构化解析）
    chunks = segment_text(parsed_text, max_chunk_size=3000)
    primary_chunk = chunks[0] if chunks else parsed_text

    # 7. 结构化解析由 AI 工作流统一负责（上传后自动触发，见步骤 11）；
    #    此处先保存原文快照，保证工作流运行前/关闭自动触发时简历页也有内容
    parsed_data = {"raw_text": parsed_text[:2000]}
    skills = None
    experience = None
    education = None

    # 8. 更新或创建 Resume 记录
    resume = db.query(Resume).filter(Resume.candidate_id == candidate_id).first()
    if resume:
        resume.file_name = file.filename
        resume.file_path = f"/resumes/{candidate_id}"
        resume.parsed_data = parsed_data
        resume.skills = skills if skills is not None else resume.skills
        resume.experience = experience or resume.experience
        resume.education = education or resume.education
    else:
        resume = Resume(
            candidate_id=candidate_id,
            file_name=file.filename,
            file_path=f"/resumes/{candidate_id}",
            parsed_data=parsed_data,
            skills=skills if isinstance(skills, list) else None,
            experience=experience,
            education=education,
        )
        db.add(resume)

    # 9. 同步更新 Candidate.resume_text
    candidate.resume_text = parsed_text
    db.commit()
    db.refresh(resume)
    db.refresh(candidate)

    # 10. 自动触发全链路工作流（简历解析→匹配→问卷挂起），后台执行不阻塞上传响应
    background_tasks.add_task(trigger_after_upload, candidate_id)

    # 11. 输出安全防护：过滤返回结果中的 PII 信息
    return _sanitize_output({
        "message": "简历上传成功",
        "candidate_id": candidate_id,
        "file_name": file.filename,
        "source_type": source_type,
        "used_ocr": used_ocr,
        "text_length": len(parsed_text),
        "parsed_text": parsed_text,
        "parsed_data": parsed_data,
        "skills": skills,
        "experience": experience,
        "education": education,
    })


@router.get("/{candidate_id}/workflow", response_model=WorkflowRunResponse)
def get_workflow_run(
    candidate_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin_interviewer)
):
    """获取候选人最近一次工作流运行记录（含真实进度与结果）"""
    workflow_run = (
        db.query(WorkflowRun)
        .filter(WorkflowRun.candidate_id == candidate_id)
        .order_by(WorkflowRun.id.desc())
        .first()
    )
    if not workflow_run:
        raise HTTPException(status_code=404, detail="该候选人暂无工作流运行记录")
    return workflow_run


@router.post("/{candidate_id}/run-workflow")
async def run_recruitment_workflow(
    candidate_id: int,
    body: dict = Body(..., description="职位要求"),
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin)
):
    """启动全链路招聘工作流。

    人岗匹配强制依赖已启用的岗位 JD：没有它直接 400。
    此前回退到 candidate.position（岗位名字符串），等于拿简历自述的职责
    去匹配简历自己，属自我印证而非真实匹配。

    自动推进到第一个挂起点（问卷待作答）后返回 waiting_human；
    后续人工事件（问卷作答、面试结果录入）由对应 API 自动恢复工作流，无需重复调用本接口。
    """
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    position_requirements = body.get("position_requirements", "")

    jd_id = body.get("job_description_id") or candidate.job_description_id
    if not db_actions.get_active_job_description(jd_id):
        raise HTTPException(
            status_code=400,
            detail="未绑定有效岗位 JD（需在岗位管理中解析并启用），无法启动人岗匹配",
        )

    # 安全检查：对工作流输入（简历文本 + 职位要求）做输入防护
    for label, text in (("简历文本", candidate.resume_text or ""), ("职位要求", position_requirements)):
        is_safe, reason = InputGuard.check(text)
        if not is_safe:
            raise HTTPException(status_code=400, detail=f"{label}未通过安全检查：{reason}")

    result = await start_workflow(candidate_id, position_requirements,
                                  job_description_id=jd_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error", "工作流启动失败"))
    return _sanitize_output(result)


@router.post("/{candidate_id}/workflow/resume")
async def resume_recruitment_workflow(
    candidate_id: int,
    body: dict = Body(..., description="人工事件：wait_type + payload"),
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin)
):
    """人工事件恢复挂起的工作流（通常由问卷提交/面试完成接口自动调用，也可手动触发）。

    wait_type: await_questionnaire（payload 含 questionnaire_id 与 responses）
               await_interview（payload 含 interview_id、round、score、feedback）
    """
    wait_type = body.get("wait_type")
    payload = body.get("payload") or {}
    if wait_type not in ("await_questionnaire", "await_interview"):
        raise HTTPException(status_code=400, detail="wait_type 必须为 await_questionnaire 或 await_interview")

    # 手动触发场景下先校验候选人存在，避免对不存在的 ID 盲目恢复
    if not db.query(Candidate).filter(Candidate.id == candidate_id).first():
        raise HTTPException(status_code=404, detail="Candidate not found")

    result = await resume_workflow(candidate_id, wait_type, payload)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error", "工作流恢复失败"))
    return _sanitize_output(result)
