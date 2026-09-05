"""招聘工作流编排层。

职责：
1. 管理带 SQLite checkpointer 的 LangGraph 图生命周期（挂起状态跨请求/重启持久化）；
2. start_workflow：从简历解析启动，驱动到第一个 interrupt（问卷挂起）或终局；
3. resume_workflow：人工事件（问卷作答/面试录入）到达后，校验挂起类型并恢复；
4. 驱动过程中实时推送 SSE 进度并持久化 WorkflowRun，供前端轮询与异常恢复。

人机交互协议（resume 时严格校验 wait_type，防止错误事件误恢复他人流程）：
- await_questionnaire  payload = {"questionnaire_id": int, "responses": {...}}
- await_interview      payload = {"interview_id": int, "round": int, "score": int, "feedback": str}
"""
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import aiosqlite
from langgraph.types import Command
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.config import settings
from src.models.database import SessionLocal, Candidate, WorkflowRun
from src.workflow.recruitment_graph import build_recruitment_graph
from src.sse.notification import notify_workflow_progress
from src.safety import OutputGuard

_graph = None
_saver: Optional[AsyncSqliteSaver] = None
_CHECKPOINT_DB = os.path.join("data", "workflow_checkpoints.db")


async def get_graph():
    """懒加载编译图（首次调用时初始化异步 checkpointer 连接）"""
    global _graph, _saver
    if _graph is None:
        os.makedirs("data", exist_ok=True)
        conn = await aiosqlite.connect(_CHECKPOINT_DB)
        _saver = AsyncSqliteSaver(conn)
        await _saver.setup()
        _graph = build_recruitment_graph(checkpointer=_saver)
    return _graph


async def purge_candidate_workflow_data(candidate_id: int) -> int:
    """删除候选人时清理 checkpointer 残留的线程状态，返回清理的线程数。

    业务表 WorkflowRun 由 ORM 级联删除负责；这里只管 checkpoint 侧，
    逐线程 best-effort 删除，单线程失败不影响其余清理。
    """
    global _saver
    with SessionLocal() as db:
        runs = db.query(WorkflowRun).filter(WorkflowRun.candidate_id == candidate_id).all()
        thread_ids = [r.results.get("thread_id") for r in runs if r.results and r.results.get("thread_id")]
    if not thread_ids:
        return 0
    await get_graph()  # 确保 saver 已初始化
    cleaned = 0
    for tid in thread_ids:
        try:
            await _saver.adelete_thread(tid)
            cleaned += 1
        except Exception:
            continue  # checkpoint 清理失败不阻断候选人删除
    return cleaned


def _new_thread_id() -> str:
    """全局唯一 thread_id：不依赖数据库 id，避免记录删除后 id 复用导致 checkpoint 串状态"""
    return f"wf-{uuid.uuid4().hex}"


def _thread_id_of(run: WorkflowRun) -> Optional[str]:
    """从 WorkflowRun.results 中恢复 thread_id"""
    if isinstance(run.results, dict):
        return run.results.get("thread_id")
    return None


def _update_run(workflow_run_id: int, **fields) -> None:
    with SessionLocal() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == workflow_run_id).first()
        if run:
            for k, v in fields.items():
                setattr(run, k, v)
            db.commit()


def _get_active_run(db, candidate_id: int) -> Optional[WorkflowRun]:
    return (
        db.query(WorkflowRun)
        .filter(WorkflowRun.candidate_id == candidate_id)
        .filter(WorkflowRun.status.in_(["running", "waiting_human"]))
        .order_by(WorkflowRun.id.desc())
        .first()
    )


def _build_result(values: Dict[str, Any], candidate: Candidate) -> Dict[str, Any]:
    """组装前端兼容的最终结果结构"""
    skill = int(values.get("skill_match_score") or 0)
    culture = int(values.get("culture_match_score") or 0)
    exp = int(values.get("experience_score") or skill)
    edu = int(values.get("education_score") or skill)
    overall = int(values.get("overall_score") or 0)
    decision = values.get("final_decision") or "待定"
    recommendation = values.get("final_recommendation") or {}

    return {
        "candidate_id": candidate.id,
        "candidate_name": candidate.name,
        "position": candidate.position,
        "final_decision": decision,
        "overall_score": overall,
        "skill_match_score": skill,
        "experience_match_score": exp,
        "education_match_score": edu,
        "culture_match_score": culture,
        "questionnaire_score": int(values.get("questionnaire_score") or 0),
        "interview_scores": values.get("interview_scores") or [],
        "workflow_progress": 100,
        "current_step": "completed",
        "analysis": {
            "skills_analysis": f"技能匹配度 {skill} 分",
            "experience_analysis": f"经验匹配度 {exp} 分",
            "education_analysis": f"教育匹配度 {edu} 分",
            "culture_analysis": f"文化契合度 {culture} 分",
            "recommendation": decision,
            "final_recommendation": recommendation,
        },
        "completed_at": datetime.utcnow().isoformat(),
    }


def _sanitize(obj):
    """递归过滤 workflow state 中的 PII（实现统一在 OutputGuard.sanitize_obj）"""
    return OutputGuard.sanitize_obj(obj)


async def _drive(graph, config, workflow_run_id: int, candidate_id: int,
                 candidate_name: str, start_input: Optional[dict] = None,
                 resume_value: Any = None) -> Dict[str, Any]:
    """驱动图运行到下一个 interrupt 或终局，途中推送 SSE 并持久化进度"""
    final_values: Dict[str, Any] = {}
    try:
        if resume_value is not None:
            stream = graph.astream(Command(resume=resume_value), config=config, stream_mode="values")
        else:
            stream = graph.astream(start_input, config=config, stream_mode="values")

        async for state in stream:
            progress = int(state.get("workflow_progress") or 0)
            step = state.get("current_step") or ""
            try:
                await notify_workflow_progress(candidate_id, progress, step, {"candidate_name": candidate_name})
            except Exception:
                pass
            _update_run(workflow_run_id, progress=progress, current_step=step)
            final_values = state

        # 检测是否停在 interrupt 挂起点
        snapshot = await graph.aget_state(config)
        interrupt_payload = None
        if snapshot.next:
            for task in snapshot.tasks:
                for intr in getattr(task, "interrupts", None) or []:
                    if intr.value:
                        interrupt_payload = intr.value
                        break
                if interrupt_payload:
                    break

        if interrupt_payload:
            return {"status": "waiting", "interrupt": interrupt_payload,
                    "progress": int(final_values.get("workflow_progress") or 0),
                    "values": final_values}
        return {"status": "completed", "values": final_values}

    except Exception as e:
        print(f"[workflow-runner] 驱动失败: {e}")
        return {"status": "error", "error": str(e), "values": final_values}


async def start_workflow(candidate_id: int, position_requirements: str = "") -> Dict[str, Any]:
    """启动一次全新的招聘工作流"""
    started_at = time.time()
    graph = await get_graph()

    with SessionLocal() as db:
        candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
        if not candidate:
            return {"status": "error", "error": "候选人不存在"}

        active = _get_active_run(db, candidate_id)
        if active:
            return {"status": "already_active", "workflow_run_id": active.id,
                    "run_status": active.status, "progress": active.progress,
                    "current_step": active.current_step}

        run = WorkflowRun(candidate_id=candidate_id, status="running", current_step="start", progress=0)
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
        candidate_name = candidate.name
        resume_text = candidate.resume_text or ""
        position = candidate.position or ""
        thread_id = _new_thread_id()
        run.results = {"thread_id": thread_id}
        db.commit()

    # 候选人进入筛选阶段
    from src.workflow import db_actions
    db_actions.set_candidate_status(candidate_id, "screening")

    try:
        await notify_workflow_progress(candidate_id, 0, "start", {"candidate_name": candidate_name})
    except Exception:
        pass

    config = {"configurable": {"thread_id": thread_id}}
    outcome = await _drive(
        graph, config, run_id, candidate_id, candidate_name,
        start_input={
            "candidate_id": candidate_id,
            "candidate_name": candidate_name,
            "resume_text": resume_text,
            "position_requirements": position_requirements or position,
            "position": position,
        },
    )

    return await _finalize_outcome(outcome, run_id, candidate_id, candidate_name, thread_id, started_at)


async def resume_workflow(candidate_id: int, wait_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """人工事件到达后恢复挂起的工作流。wait_type 必须与当前 interrupt 类型一致"""
    started_at = time.time()
    graph = await get_graph()

    with SessionLocal() as db:
        candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
        if not candidate:
            return {"status": "error", "error": "候选人不存在"}
        candidate_name = candidate.name

        run = _get_active_run(db, candidate_id)
        if not run or run.status != "waiting_human":
            return {"status": "error", "error": "该候选人没有等待人工处理的工作流"}
        run_id = run.id
        thread_id = _thread_id_of(run)
        if not thread_id:
            return {"status": "error", "error": "工作流线程标识丢失，无法恢复"}

    config = {"configurable": {"thread_id": thread_id}}

    # 校验挂起类型，防止错误事件（如把问卷答案发给面试挂起点）误恢复
    snapshot = await graph.aget_state(config)
    current_interrupt = None
    for task in snapshot.tasks:
        for intr in getattr(task, "interrupts", None) or []:
            if intr.value:
                current_interrupt = intr.value
                break
        if current_interrupt:
            break

    if not current_interrupt:
        return {"status": "error", "error": "工作流当前不在挂起状态，可能已被处理"}
    if current_interrupt.get("type") != wait_type:
        return {"status": "error",
                "error": f"事件类型不匹配：流程等待 {current_interrupt.get('type')}，收到 {wait_type}"}
    if wait_type == "await_interview" and current_interrupt.get("round") != payload.get("round"):
        return {"status": "error",
                "error": f"面试轮次不匹配：流程等待第 {current_interrupt.get('round')} 轮，收到第 {payload.get('round')} 轮"}

    _update_run(run_id, status="running")
    outcome = await _drive(graph, config, run_id, candidate_id, candidate_name, resume_value=payload)
    return await _finalize_outcome(outcome, run_id, candidate_id, candidate_name, thread_id, started_at)


async def _finalize_outcome(outcome: Dict[str, Any], run_id: int,
                            candidate_id: int, candidate_name: str,
                            thread_id: str, started_at: float) -> Dict[str, Any]:
    """根据驱动结果落定 WorkflowRun 状态并返回 API 响应"""
    with SessionLocal() as db:
        candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()

        if outcome["status"] == "waiting":
            interrupt_payload = outcome["interrupt"]
            wait_type = interrupt_payload.get("type", "unknown")
            _update_run(run_id, status="waiting_human", current_step=wait_type,
                        results={"thread_id": thread_id, "interrupt": interrupt_payload})
            await notify_workflow_progress(candidate_id, outcome.get("progress", 0), wait_type,
                                           {"waiting": True, "candidate_name": candidate_name})
            return _sanitize({
                "status": "waiting_human",
                "workflow_run_id": run_id,
                "wait_type": wait_type,
                "interrupt": interrupt_payload,
                "progress": outcome.get("progress", 0),
                "message": {
                    "await_questionnaire": "问卷已生成，等待候选人作答后自动进入面试排期",
                    "await_interview": f"第{interrupt_payload.get('round')}轮面试已自动排期，等待面试结果录入",
                }.get(wait_type, "等待人工处理"),
            })

        if outcome["status"] == "error":
            _update_run(run_id, status="failed", results={"thread_id": thread_id, "error": outcome.get("error")})
            return {"status": "error", "workflow_run_id": run_id, "error": outcome.get("error")}

        # completed
        result = _build_result(outcome.get("values") or {}, candidate)
        result["thread_id"] = thread_id
        _update_run(run_id, status="completed", current_step="completed", progress=100, results=result)

        # 评估质量追踪（失败不影响主流程）
        try:
            from src.evaluation import evaluation_tracker
            evaluation_tracker.record(
                candidate_id=candidate_id,
                scores={
                    "overall_score": result.get("overall_score", 0),
                    "skill_match_score": result.get("skill_match_score", 0),
                    "experience_match_score": result.get("experience_match_score", 0),
                    "education_match_score": result.get("education_match_score", 0),
                    "culture_match_score": result.get("culture_match_score", 0),
                },
                decision=result.get("final_decision", ""),
                duration_ms=(time.time() - started_at) * 1000,
            )
        except Exception as e:
            print(f"[workflow-runner] 评估追踪记录失败: {e}")

        return _sanitize({"status": "completed", "workflow_run_id": run_id, **result})


async def trigger_after_upload(candidate_id: int) -> None:
    """简历上传后的自动触发入口（BackgroundTasks 调用，异常全部吞掉不影响上传）"""
    if not getattr(settings, "AUTO_START_WORKFLOW", True):
        return
    try:
        with SessionLocal() as db:
            candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
            position = candidate.position if candidate else ""
        await start_workflow(candidate_id, position_requirements=position or "")
    except Exception as e:
        print(f"[workflow-runner] 上传后自动触发工作流失败: {e}")
