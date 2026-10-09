"""招聘工作流编排层。

职责：
1. 管理带 checkpointer 的 LangGraph 图生命周期（挂起状态跨请求/重启持久化）；
2. start_workflow：从简历解析启动，驱动到第一个 interrupt（问卷挂起）或终局；
3. resume_workflow：人工事件（问卷作答/面试录入）到达后，校验挂起类型并恢复；
4. 驱动过程中实时推送 SSE 进度并持久化 WorkflowRun，供前端轮询与异常恢复。

人机交互协议（resume 时严格校验 wait_type，防止错误事件误恢复他人流程）：
- await_questionnaire  payload = {"questionnaire_id": int, "responses": {...}}
- await_interview      payload = {"interview_id": int, "round": int, "score": int, "feedback": str}

**checkpointer 后端按 DATABASE_URL 选择**：
- postgres：与应用同库，多副本共享同一份挂起状态（横向扩副本的前提）
- sqlite  ：本地开发用本地文件（单进程）

这里**不做**「postgres 连不上就悄悄退回本地文件」的降级——那正是要修掉的静默断链：
多副本部署时每个容器各写各的本地文件，A 副本启动的流程在 B 副本 resume 会找不到
状态，表现为"进行中的流程莫名卡住"，且日志里没有明显错误。所以 postgres 模式下
缺依赖/连不上就直接报错，让问题在部署阶段暴露。
"""
import asyncio
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
from src.workflow import db_actions
from src.services import budget
from src.workflow.recruitment_graph import build_recruitment_graph, compute_overall
from src.sse.notification import notify_workflow_progress
from src.safety import OutputGuard

_graph = None
_saver: Optional[Any] = None
_pool: Optional[Any] = None          # postgres 模式的连接池
_sqlite_conn: Optional[Any] = None   # sqlite 模式的 aiosqlite 连接
_CHECKPOINT_DB = os.path.join("data", "workflow_checkpoints.db")

# 连接池上限：每副本最多这么多条 checkpointer 连接
_PG_POOL_MAX = 10


def _is_postgres(url: str) -> bool:
    """判断连接串是否指向 PostgreSQL（忽略 SQLAlchemy 的 `+driver` 后缀）"""
    scheme = url.split("://", 1)[0]
    return scheme.split("+", 1)[0] in ("postgresql", "postgres")


def _pg_conninfo(url: str) -> str:
    """把 SQLAlchemy 风格 URL 转成 psycopg 可用的连接串。

    SQLAlchemy 允许 `postgresql+psycopg2://...` 这种带驱动后缀的写法，psycopg 不认。
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    return f"{scheme.split('+', 1)[0]}://{rest}"


def _loop_is_incompatible(platform_name: str, loop) -> bool:
    """纯函数：给定平台名与事件循环，判断 psycopg 异步能否使用。

    抽成纯函数是为了可测——否则只能 monkeypatch 全局 `os.name`，
    那会影响该模块之外所有读 os.name 的代码。
    """
    if platform_name != "nt" or loop is None:
        return False
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    return proactor is not None and isinstance(loop, proactor)


def _psycopg_incompatible_loop() -> bool:
    """当前事件循环是否不被 psycopg 异步模式支持。

    psycopg3 的异步连接只支持 SelectorEventLoop；Windows 的默认实现
    ProactorEventLoop 会直接报错，而 uvicorn 在 Windows 上用的正是 Proactor
    （见 uvicorn/loops/asyncio.py）。真实部署是 Linux/容器，默认 Selector，不受影响；
    这里做前置检查是为了**快速失败并说清原因**——否则表现为 30 秒 PoolTimeout，
    真实原因只出现在连接池的后台日志里，极难定位。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return _loop_is_incompatible(os.name, loop)


async def get_graph():
    """懒加载编译图（首次调用时初始化 checkpointer）。"""
    global _graph, _saver, _pool, _sqlite_conn
    if _graph is None:
        if _is_postgres(settings.DATABASE_URL):
            if _psycopg_incompatible_loop():
                raise RuntimeError(
                    "Postgres checkpointer 需要 Selector 事件循环，"
                    "但当前是 Windows 默认的 ProactorEventLoop（psycopg 异步不支持）。"
                    "请在 Linux/容器内运行（见 docker-compose.yml），"
                    "或在启动前改用 SelectorEventLoop。"
                    "如需在 Windows 上本地开发，把 DATABASE_URL 设为 sqlite 即可。"
                )
            # 延迟导入：只装 sqlite 的环境不该被迫依赖 psycopg
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool

            _pool = AsyncConnectionPool(
                conninfo=_pg_conninfo(settings.DATABASE_URL),
                open=False,
                max_size=_PG_POOL_MAX,
                # 连不上时快点失败，别让人对着 30 秒超时猜
                timeout=10.0,
                # autocommit 与 dict_row 是 AsyncPostgresSaver 的硬要求
                kwargs={"autocommit": True, "row_factory": dict_row,
                        "connect_timeout": 10},
            )
            try:
                # open() 本身也可能失败（连不上/参数错），必须一起纳入清理范围：
                # 漏掉它会让池对象残留，其后台任务拖住进程不退出
                await _pool.open()
                _saver = AsyncPostgresSaver(_pool)
                await _saver.setup()
            except BaseException:
                # 初始化失败必须关掉池：池内部有后台重连任务，
                # 不关闭会让进程挂住不退出（表现为"启动卡死而非报错退出"）
                await _pool.close()
                _pool = None
                _saver = None
                raise
        else:
            os.makedirs("data", exist_ok=True)
            _sqlite_conn = await aiosqlite.connect(_CHECKPOINT_DB)
            _saver = AsyncSqliteSaver(_sqlite_conn)
            await _saver.setup()
        _graph = build_recruitment_graph(checkpointer=_saver)
    return _graph


async def close_graph() -> None:
    """释放 checkpointer 资源（应用退出时调用）。

    sqlite 连接也必须显式关闭：aiosqlite 用后台线程转发调用，连接不关时
    该线程会在事件循环关闭后抛 "Event loop is closed"。
    """
    global _graph, _saver, _pool, _sqlite_conn
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
    if _sqlite_conn is not None:
        try:
            await _sqlite_conn.close()
        except Exception:
            pass
    _pool = None
    _sqlite_conn = None
    _saver = None
    _graph = None


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
    unconstrained = values.get("unconstrained_dimensions") or []
    unconstrained = list(unconstrained)

    def _score(key: str, fallback: Optional[float] = None) -> Optional[int]:
        """岗位未设限、或流程没走到这一步时返回 None，而不是编一个分数出来。

        旧实现在此处回落到技能分，会让"从未评估过的维度"显示成实打实的分数，
        与 compute_overall 正确排除它的做法自相矛盾——展示和算分必须一致。
        """
        v = values.get(key)
        if v is None:
            return None
        return int(v)

    def _text(label: str, key: str) -> str:
        if key in unconstrained:
            return f"{label}：岗位未设限，不参与评分"
        value = _score(key)
        return f"{label}{value} 分" if value is not None else f"{label}：未评估"

    skill = _score("skill_match_score")
    culture = _score("culture_match_score")
    exp = _score("experience_score")
    edu = _score("education_score")
    overall = int(values.get("overall_score") or 0)
    decision = values.get("final_decision") or "待定"
    recommendation = values.get("final_recommendation") or {}

    return {
        "candidate_id": candidate.id,
        "candidate_name": candidate.name,
        "position": candidate.position,
        "job_description_id": candidate.job_description_id,
        "jd_source": values.get("jd_source"),
        # 评分口径版本：提示词与权重变更后，新旧分数不可比，回溯时需要区分
        "scoring_version": settings.SCORING_VERSION,
        "final_decision": decision,
        "overall_score": overall,
        "skill_match_score": skill,
        "experience_match_score": exp,
        "education_match_score": edu,
        "culture_match_score": culture,
        "questionnaire_score": int(values.get("questionnaire_score") or 0),
        "interview_scores": values.get("interview_scores") or [],
        "unconstrained_dimensions": unconstrained,
        "needs_review": bool(values.get("needs_review")),
        "review_reason": values.get("review_reason"),
        "review_detail": values.get("review_detail"),
        "assessed_dimensions": recommendation.get("assessed_dimensions") or [],
        "workflow_progress": 100,
        "current_step": "completed",
        "analysis": {
            "skills_analysis": _text("技能匹配度", "skill_match_score"),
            "experience_analysis": _text("经验匹配度", "experience_score"),
            "education_analysis": _text("教育匹配度", "education_score"),
            "culture_analysis": _text("文化契合度", "culture_match_score"),
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

    except budget.BudgetExceeded as e:
        # 预算耗尽：必须在兜底 except Exception 之前精确捕获。
        # 此时后续节点不再执行，但中断前已写入 Evaluation 的评分保留——
        # 预算耗尽不是数据错误，已花钱得到的结论应当留下。
        print(f"[workflow-runner] 成本预算耗尽，工作流中止: {e}")
        return {"status": "budget_exhausted", "error": str(e), "values": final_values}
    except Exception as e:
        print(f"[workflow-runner] 驱动失败: {e}")
        return {"status": "error", "error": str(e), "values": final_values}


async def start_workflow(candidate_id: int, position_requirements: str = "",
                         job_description_id: Optional[int] = None) -> Dict[str, Any]:
    """启动一次全新的招聘工作流。

    强制绑定 JD：没有 active 且解析成功的岗位 JD 就直接返回 error。
    此前回退到 candidate.position（岗位名字符串），等于拿简历自述的职责
    去匹配简历自己，是自我印证而非真实的人岗匹配。
    """
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

        # 岗位 JD 必须在创建 run 之前校验：没有它就不该留下"已启动"的运行记录
        jd_id = job_description_id or candidate.job_description_id
        jd = db_actions.get_active_job_description(jd_id)
        if not jd:
            return {"status": "error",
                    "error": "未绑定有效岗位 JD（需 status=active 且解析成功），无法启动人岗匹配"}

        run = WorkflowRun(candidate_id=candidate_id, status="running", current_step="start", progress=0)
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
        candidate_name = candidate.name
        resume_text = candidate.resume_text or ""
        position = candidate.position or ""
        thread_id = _new_thread_id()
        run.results = {"thread_id": thread_id, "jd_profile": jd.get("parsed_data"),
                       "scoring_version": settings.SCORING_VERSION}
        db.commit()

    # 候选人进入筛选阶段
    # 注意：这里不要写局部 `from src.workflow import db_actions`，
    # 会把模块级同名导入遮蔽成局部变量，导致上面的 JD 校验抛 UnboundLocalError
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
            "job_description_id": jd["id"],
            "jd_profile": jd.get("parsed_data"),
            "jd_source": "job_description",
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

        if outcome["status"] == "budget_exhausted":
            # 转人工：候选人停在"待人工评估"，不产出任何招聘决策。
            # assessed 让 HR 知道已经评了什么、还差什么。
            values = outcome.get("values") or {}
            _overall, assessed = compute_overall(values)
            db_actions.set_candidate_status(candidate_id, "pending_manual")
            db_actions.upsert_talent_pool(
                candidate_id, ["预算暂停", "待人工评估"],
                f"成本预算耗尽，流程中止于 {values.get('current_step') or '未知环节'}。"
                f"已完成维度：{', '.join(assessed) or '无'}。请人工评估或提高预算后重跑。",
            )
            _update_run(
                run_id, status="budget_halted",
                current_step=values.get("current_step") or "budget_halted",
                results={
                    "thread_id": thread_id,
                    "budget_halted": True,
                    "error": outcome.get("error"),
                    "assessed_dimensions": assessed,
                    "scoring_version": settings.SCORING_VERSION,
                    # 提示 HR 可以直接重跑：budget_halted 不算活跃运行，不会撞 _get_active_run
                    "can_rerun": True,
                },
            )
            try:
                from src.sse.notification import notify_budget_halted
                await notify_budget_halted(candidate_id, candidate_name,
                                           values.get("current_step") or "未知环节", assessed)
            except Exception:
                pass
            return {
                "status": "budget_halted",
                "candidate_id": candidate_id,
                "budget_halted": True,
                "error": outcome.get("error"),
                "assessed_dimensions": assessed,
                "message": "LLM 成本预算已用尽，流程已中止，请人工评估或在提高预算后重新运行",
            }

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
    """简历上传后的自动触发入口（BackgroundTasks 调用，异常全部吞掉不影响上传）

    未绑定有效 JD 时不启动：人岗匹配现在强制依赖结构化 JD，
    没绑定就明确推 jd_missing 通知引导前端补录，而不是静默什么都不发生。
    """
    if not getattr(settings, "AUTO_START_WORKFLOW", True):
        return
    try:
        with SessionLocal() as db:
            candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
            position = candidate.position if candidate else ""
            name = candidate.name if candidate else ""
            jd_id = candidate.job_description_id if candidate else None
        if not db_actions.get_active_job_description(jd_id):
            from src.sse.notification import notify_jd_missing
            await notify_jd_missing(candidate_id, name)
            return
        await start_workflow(candidate_id, position_requirements=position or "")
    except Exception as e:
        print(f"[workflow-runner] 上传后自动触发工作流失败: {e}")
