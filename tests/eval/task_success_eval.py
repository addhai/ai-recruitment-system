"""任务成功率评测：用**可控故障注入**测量「单点故障下，任务还能不能产出可用结论」。

## 为什么必须注入，而不是靠随机失败

真实 LLM 调用失败率约 0.4%（实测 507 次里 2 次）。N=15 的真实任务期望失败 0.06 次——
**测不出任何差别**。所以主实验用 mock 在指定节点注入指定故障：
n 可控、归因明确、零 API 成本、同种子可复现。

## 注入点为什么选 `_invoke_raw`

`src/services/llm_invoke._invoke_raw` 是所有 LLM 调用的最底层。
在它上面打桩，意味着 invoke_json 的**降级、埋点、预算检查、熔断全是真实生产代码**，
不是我们在这里模拟出来的。注入点选高一层（比如 mock 整个 `_llm_json`），
测的就是我们自己写的那套 mock，不是生产行为。

## 故障模式

| 模式 | 注入方式 | 期望的现状行为 |
|---|---|---|
| `timeout` | 抛 APITimeoutError | invoke_json 降级到 default，任务继续 |
| `upstream_5xx` | 抛带 status_code=500 的异常 | 同上 |
| `bad_json` | 返回非 JSON 内容 | JsonOutputParser 失败 → 降级 |
| `bad_schema` | 返回合法 JSON 但类型不对（score=null） | **节点代码 float(None) 崩 → 整任务死** |
| `budget_exhausted` | 抛 BudgetExceeded | 转人工 budget_halted |

## 口径

- 一个"任务"= 一次 `start_workflow` 跑到下一个 interrupt 或终局
- **存活**：终态 ∈ {waiting, completed, budget_halted}（没有被异常打断）
- **无降级**：全程没有任何维度用了兜底分（LLM 挂了却产出 70 分那种）
- **部分有用**：任务死了，但已评估维度被保留下来（改进后应显著上升）
- `waiting_human` 是设计内挂起，**不是失败**

用法：
    python tests/eval/task_success_eval.py --n 20 --out task_success_before.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import tempfile
import uuid
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---- 隔离环境：必须在 import src 之前设置 ----
_TMPDIR = tempfile.mkdtemp(prefix="task_success_eval_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_TMPDIR) / "eval.db")
os.environ.setdefault("SECRET_KEY", "eval-secret")
# 刻意**不**设置 LLM_API_KEY：
# os.environ 的优先级高于 .env，而 src.config.settings 是 import 时创建的
# 模块级单例——这里塞个占位 key，会把真 key 顶掉且事后无法补救
# （真实任务会全部 401 失败，还把熔断器打开，测出来的是假数据）。
# 本文件全程 mock 掉 _invoke_raw，本来就不需要 key。
os.environ["LLM_BUDGET_ENABLED"] = "false"
os.environ["RERANK_ENABLED"] = "false"
os.environ["LLM_MAX_CONCURRENCY"] = "0"
sys.path.insert(0, str(REPO_ROOT))

from langchain_core.messages import AIMessage  # noqa: E402

from src.models.database import Candidate, Evaluation, JobDescription, SessionLocal, WorkflowRun, init_db  # noqa: E402
from src.services import budget, llm_invoke  # noqa: E402
from src.workflow import runner  # noqa: E402

# --------------------------------------------------------------------------
# 故障模式
# --------------------------------------------------------------------------

EVAL_SITES = [
    "parse_resume",
    "extract_skills",
    "evaluate_skill_match",
    "evaluate_experience",
    "evaluate_education",
    "assess_cultural_fit",
]

MODES = ["timeout", "upstream_5xx", "bad_json", "bad_schema",
         "db_error", "budget_exhausted"]

# 节点 → 它写库时用的维度名。db_error 模式据此在写库那一刻注入故障，
# 制造一次**真正会让任务崩掉**的失败——类型防御只管 LLM 输出的类型，
# 管不到写库/业务逻辑的异常。partial 终态只能靠这类故障验证。
SITE_DIMENSION = {
    "parse_resume": None,          # 它写的是 save_resume_parsed，不是 upsert_evaluation
    "extract_skills": None,
    "evaluate_skill_match": "技能匹配",
    "evaluate_experience": "经验匹配",
    "evaluate_education": "教育匹配",
    "assess_cultural_fit": "文化契合",
}

# 每种故障模式可用的注入节点。db_error 只能打在会写 Evaluation 的节点上
# （前两个节点写的是 resume 解析，不是评估记录）。
MODE_SITES = {
    "db_error": ["evaluate_skill_match", "evaluate_experience",
                 "evaluate_education", "assess_cultural_fit"],
}


class _APITimeoutError(Exception):
    """与 openai.APITimeoutError 同名——extract_llm_error 按类名拼错误信息。"""


class _APIStatusError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.response = type(
            "_R", (), {"status_code": status_code,
                       "text": json.dumps({"error": {"message": message}})}
        )()


# --------------------------------------------------------------------------
# 健康路径的正常返回：让未注入的调用都成功，流程能一路走到问卷挂起
# --------------------------------------------------------------------------

HEALTHY: dict[str, dict] = {
    "parse_resume": {
        "basic_info": {"name": "注入测试候选人", "experience_years": 5},
        "skills": ["Python", "FastAPI", "PostgreSQL"],
        "experience": [{"company": "某公司", "role": "后端工程师", "duration": "3年",
                        "description": "负责招聘系统后端开发"}],
        "education": [{"school": "某大学", "degree": "本科", "major": "计算机"}],
        "projects": [{"name": "招聘系统", "description": "AI 招聘平台"}],
    },
    "extract_skills": {
        "skills_technical": ["Python", "FastAPI", "PostgreSQL"],
        "skills_soft": ["沟通"],
        "domain_knowledge": ["招聘系统"],
    },
    "evaluate_skill_match": {
        "matched_skills": ["Python", "FastAPI"], "missing_skills": ["K8s"],
        "score": 85, "reason": "命中主要硬性技能",
    },
    "evaluate_experience": {
        "score": 80, "analysis": "相关经验充分", "years_match": True,
    },
    "evaluate_education": {
        "score": 75, "analysis": "学历满足要求",
    },
    "assess_cultural_fit": {
        "evidence": ["主导过跨团队项目交付"], "score": 80,
        "reasons": ["有明确的协作与交付证据"],
    },
    "questionnaire_stage": {
        "questions": [
            {"question": "请描述一次你主导的技术方案设计", "type": "open"},
            {"question": "你如何保证系统稳定性？", "type": "open"},
        ],
    },
    "score_questionnaire": {"score": 75, "feedback": "作答完整"},
    "generate_interview_questions": {
        "questions": [{"question": "讲讲你的系统设计经历", "type": "open"}],
    },
    "generate_hiring_decision": {
        "decision": "hire", "reason": "各维度达标", "hiring_decision": "hire",
    },
}

# 每个评估节点的兜底分（与 recruitment_graph 里的 default 对齐）
FALLBACK_SCORES = {
    "evaluate_skill_match": 70,
    "evaluate_experience": 70,
    "evaluate_education": 70,
    "assess_cultural_fit": 70,
}


def _meta(call_site: str) -> dict:
    return {
        "model": "mock-model", "model_served": "mock-model", "base_url": "http://mock",
        "input_tokens": 100, "output_tokens": 50, "cost": 0.0, "latency_ms": 10,
    }


class _Recorder:
    """记录本次任务里每次调用发生了什么，用于判定「哪一维用了兜底分」。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.injected_at: str | None = None
        self.injected_mode: str | None = None
        self.degraded_sites: list[str] = []


def _make_fake_invoke_raw(target_site: str | None, mode: str | None, rec: _Recorder):
    async def fake_invoke_raw(prompt_text, variables, call_site, candidate_id, client=None):
        rec.calls.append(call_site)
        if target_site and call_site == target_site and rec.injected_at is None:
            if mode == "db_error":
                # 这一次的 LLM 调用本身要**成功**：故障发生在紧随其后的写库上。
                # 所以这里不能标记 injected_at——否则写库注入会被"已注入"的判断挡掉。
                payload = HEALTHY.get(call_site, {})
                return AIMessage(content=json.dumps(payload, ensure_ascii=False)), _meta(call_site)
            rec.injected_at = call_site
            rec.injected_mode = mode
            if mode == "timeout":
                raise _APITimeoutError("Request timed out.")
            if mode == "upstream_5xx":
                raise _APIStatusError(500, "Internal Server Error")
            if mode == "bad_json":
                return AIMessage(content="这不是 JSON，是一段散文。"), _meta(call_site)
            if mode == "bad_schema":
                # 合法 JSON，但字段类型不对：下游 float(None) 会炸
                return AIMessage(content=json.dumps(
                    {"score": None, "reasons": None, "evidence": None,
                     "matched_skills": "not-a-list", "missing_skills": 123},
                    ensure_ascii=False)), _meta(call_site)
            if mode == "budget_exhausted":
                raise budget.BudgetExceeded("注入：预算耗尽")
        payload = HEALTHY.get(call_site, {})
        return AIMessage(content=json.dumps(payload, ensure_ascii=False)), _meta(call_site)
    return fake_invoke_raw


def _make_failing_upsert(target_site: str | None, rec: _Recorder):
    """db_error 模式：在指定维度写库时抛异常，制造一次真正崩掉任务的失败。"""
    from src.workflow import recruitment_graph as rg

    dimension = SITE_DIMENSION.get(target_site or "")
    original = rg.db.upsert_evaluation

    def failing(candidate_id, dim, score, comment=""):
        if dimension and dim == dimension and rec.injected_at is None:
            rec.injected_at = target_site
            rec.injected_mode = "db_error"
            raise RuntimeError("注入：写库失败")
        return original(candidate_id, dim, score, comment)

    return failing


# --------------------------------------------------------------------------
# 固定装置
# --------------------------------------------------------------------------

def _ensure_jd() -> int:
    """建一个 active 且已解析的 JD（整个评测共用一个）。

    JD 结构必须与真实解析产物一致：`basic.education_required` / `basic.experience_years_required`
    才是节点读取的键。写错位置会让 `is_unconstrained()` 判定"JD 没写"，
    于是 evaluate_education 直接早返回、**根本不调 LLM**——注入点形同虚设。
    """
    with SessionLocal() as db:
        existing = db.query(JobDescription).filter(
            JobDescription.status == "active",
            JobDescription.parse_status == "parsed").first()
        if existing:
            return existing.id
        jd = JobDescription(
            title="后端工程师",
            raw_text="负责招聘系统后端开发，要求熟悉 Python/FastAPI/PostgreSQL。",
            status="active",
            parse_status="parsed",
            parsed_data={
                "basic": {
                    "department": "技术部",
                    "location": "北京",
                    "employment_type": "全职",
                    "headcount": 1,
                    # 这两项必须明确非"不限制"，否则对应维度会早返回、跳过 LLM 调用
                    "experience_years_required": "3年以上",
                    "experience_years_preferred": "5年以上",
                    "education_required": "本科及以上学历",
                    "education_preferred": "",
                },
                "responsibilities": ["负责招聘系统后端开发与维护", "负责 AI 评估链路建设"],
                # required_skills 必须是 dict 列表（{"skill":..., "evidence":...}），
                # 不是字符串列表——_jd_required_skills 直接对元素取 .get("skill")，
                # 给字符串会抛 'str' object has no attribute 'get'，整个任务直接死。
                "required_skills": [
                    {"skill": "Python", "evidence": "熟练使用 Python"},
                    {"skill": "FastAPI", "evidence": "有 FastAPI 项目经验"},
                    {"skill": "PostgreSQL", "evidence": "熟悉 PostgreSQL"},
                ],
                "preferred_skills": [
                    {"skill": "Kubernetes", "evidence": "了解容器编排"},
                ],
                "tech_stack": ["Python", "FastAPI", "PostgreSQL", "Redis"],
                "domain_knowledge": ["招聘系统", "RAG 检索"],
                "soft_skills": ["跨团队协作"],
                "culture_values": ["跨团队协作", "结果导向"],
                "keywords": ["招聘", "后端", "AI"],
            },
        )
        db.add(jd)
        db.commit()
        db.refresh(jd)
        return jd.id


def _new_candidate(jd_id: int) -> int:
    """每个场景建一个**新**候选人。

    刻意不删旧的：`parse_resume` 是幂等的（读 resumes 表判断是否已结构化解析），
    而 SQLite 会复用被删掉的行 id。若清理不彻底，下一个场景会拿到同一个 id、
    读到上一轮残留的解析结果，于是**跳过 LLM 调用**——注入点永远打不中，
    评测会假绿成"注入无效"。
    """
    with SessionLocal() as db:
        c = Candidate(
            name=f"注入候选人-{uuid.uuid4().hex[:6]}",
            email=f"inject_{uuid.uuid4().hex[:8]}@t.com",
            resume_text="5 年 Python 后端经验，主导过招聘系统开发，熟悉 FastAPI 与 PostgreSQL，"
                        "有跨部门协作与交付成果。",
            position="后端工程师",
            job_description_id=jd_id,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id


# --------------------------------------------------------------------------
# 单次运行
# --------------------------------------------------------------------------

async def _run_once(target_site: str | None, mode: str | None,
                    timeout: float = 45.0) -> dict:
    rec = _Recorder()
    original = llm_invoke._invoke_raw
    llm_invoke._invoke_raw = _make_fake_invoke_raw(target_site, mode, rec)
    original_upsert = None
    if mode == "db_error":
        from src.workflow import recruitment_graph as rg
        original_upsert = rg.db.upsert_evaluation
        rg.db.upsert_evaluation = _make_failing_upsert(target_site, rec)
    try:
        cid = _new_candidate(_ensure_jd())
        # 每个场景都必须有超时上限：挂起是「一种故障」，
        # 不该让整个评测卡死在那儿。超时本身记为一次未存活样本。
        out = await asyncio.wait_for(
            runner.start_workflow(cid, position_requirements="后端工程师"),
            timeout=timeout,
        )
        status = out.get("status")
        error = out.get("error")
        # 从库里读回 run 的终态与结果
        with SessionLocal() as db:
            run = db.query(WorkflowRun).filter(
                WorkflowRun.candidate_id == cid).order_by(WorkflowRun.id.desc()).first()
            run_status = run.status if run else None
            results = run.results if run and run.results else {}
            cand = db.query(Candidate).filter(Candidate.id == cid).first()
            cand_status = cand.status if cand else None
            # 已评估维度数：从 Evaluation 表读，而不是从 results 读——
            # 节点里每评完一个维度就 upsert 一次，所以这张表反映的是
            # **实际落到库的结论**，与 run 的终态无关。这正是"沉没成本"的度量。
            evaluated = db.query(Evaluation).filter(
                Evaluation.candidate_id == cid).all()
            evaluated_dims = sorted({e.dimension for e in evaluated})
    except asyncio.TimeoutError:
        status, error = "harness_timeout", f"场景超过 {timeout}s 未结束"
        run_status, results, cand_status = None, {}, None
        evaluated_dims = []
    except Exception as e:  # 驱动本身炸了也算一次失败样本
        status, error = "harness_error", f"{type(e).__name__}: {e}"
        run_status, results, cand_status = None, {}, None
        evaluated_dims = []
    finally:
        llm_invoke._invoke_raw = original
        if original_upsert is not None:
            from src.workflow import recruitment_graph as rg
            rg.db.upsert_evaluation = original_upsert

    survived = run_status in ("waiting_human", "completed", "budget_halted", "partial")
    # 注入点对应的维度是否用了兜底分。
    #
    # - 调用失败类（timeout/5xx/bad_json）：invoke_json 捕获后原样返回 default
    #   → 该维度必然是兜底分；
    # - 类型违规类（bad_schema）：模型返回了但字段类型不对，节点强转后
    #   同样落到兜底分（修复前是直接崩，压根没有分）；
    # - 预算耗尽：任务是**主动**停下（转人工），不是降级，不计入。
    #
    # 这个定义不依赖生产代码，所以改进前/后可以用同一把尺子量。
    degraded = []
    if rec.injected_at and rec.injected_mode in (
            "timeout", "upstream_5xx", "bad_json", "bad_schema"):
        degraded.append(rec.injected_at)

    # 有效性守卫：注入场景里注入点必须真的被打中。
    # 没打中 = harness 失效（节点早返回、id 复用残留等），必须显式暴露，
    # 否则会被当成「注入后任务仍然存活」而把评测结果做成假绿。
    injection_fired = True
    if mode is not None and rec.injected_at is None:
        injection_fired = False

    return {
        "mode": mode,
        "target_site": target_site,
        "status": status,
        "run_status": run_status,
        "candidate_status": cand_status,
        "error": (error or "")[:300] if error else None,
        "survived": survived,
        "injection_fired": injection_fired,
        "degraded_sites": degraded,
        "degraded_dims_reported": list(results.get("degraded_dimensions") or []),
        "evaluated_dims": evaluated_dims,
        "evaluated_count": len(evaluated_dims),
        "assessed_dimensions": results.get("assessed_dimensions") or [],
        "has_partial_fields": any(
            k in results for k in ("failed_step", "failed_call_site", "cost_spent", "elapsed_ms")
        ),
        "failed_step": results.get("failed_step"),
        "cost_spent": results.get("cost_spent"),
        "calls": len(rec.calls),
        "call_seq": list(rec.calls),
        "injected_at": rec.injected_at,
        "injected_mode": rec.injected_mode,
        "reached_eval_end": run_status == "waiting_human",
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="每种故障模式的样本数")
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--out", default="task_success_report.json")
    ap.add_argument("--include-control", action="store_true",
                    help="额外跑一轮无注入的对照组")
    ap.add_argument("--hang-dump", type=float, default=0,
                    help="N 秒后若仍未结束，转储所有线程栈并退出（0=关闭）")
    ap.add_argument("--timeout", type=float, default=45.0,
                    help="单场景超时上限（秒）")
    args = ap.parse_args()

    # 场景挂起时把栈打出来，而不是无声卡死
    if args.hang_dump:
        import faulthandler
        faulthandler.dump_traceback_later(args.hang_dump, exit=True)

    init_db()
    # checkpointer 也隔离到临时目录，不碰仓库里的 data/workflow_checkpoints.db
    runner._CHECKPOINT_DB = str(Path(_TMPDIR) / "checkpoints.db")

    records: list[dict] = []

    async def run_all():
        try:
            # 对照组：完全不注入，确认 harness 本身能跑通正常路径
            if args.include_control:
                r = await _run_once(None, None, timeout=args.timeout)
                r["mode"] = "control"
                r["target_site"] = None
                records.append(r)
                print(f"  control          -> run_status={r['run_status']:<14} "
                      f"survived={r['survived']}")

            for mode in MODES:
                # 站点轮转 + 种子打乱：保证每个模式内节点覆盖均匀，
                # 同时顺序由种子决定，可复现
                sites = MODE_SITES.get(mode, EVAL_SITES)
                pool = []
                while len(pool) < args.n:
                    pool.extend(sites)
                pool = pool[: args.n]
                # 注意：不能用内置 hash(str)——它带每进程随机盐，会破坏可复现性。
                # 用 crc32 是稳定的跨进程散列。
                random.Random(args.seed + zlib.crc32(mode.encode()) % 1000).shuffle(pool)
                for i, site in enumerate(pool):
                    r = await _run_once(site, mode, timeout=args.timeout)
                    r["sample"] = i
                    records.append(r)
                    flag = "" if r["injection_fired"] else "  <-- 注入未命中(harness 失效)"
                    print(f"  {mode:<18}@{site:<24} -> run_status={str(r['run_status']):<14} "
                          f"survived={r['survived']}{flag}")
        finally:
            # 不关 checkpointer 的话，aiosqlite 的连接线程会让进程在退出时挂死
            await runner.close_graph()

    asyncio.run(run_all())

    # ---- 汇总 ----
    def agg(rs: list[dict]) -> dict:
        n = len(rs)
        if not n:
            return {"n": 0}
        surv = sum(1 for r in rs if r["survived"])
        clean = sum(1 for r in rs if r["survived"] and not r["degraded_sites"])
        # 有结论：任务跑完/中断，但库里确实落下了维度的评分（结论可用）
        useful = sum(1 for r in rs if r["survived"] and r["evaluated_count"] > 0)
        # 部分有用：任务没跑完，但已评估的维度还在库里（沉没成本没白付）
        partial_credit = sum(
            1 for r in rs if not r["survived"] and r["evaluated_count"] > 0)
        not_fired = sum(1 for r in rs if not r.get("injection_fired", True))
        degraded_reported = sum(1 for r in rs if r.get("degraded_dims_reported"))
        return {
            "n": n,
            "survived": surv,
            "survival_rate": round(surv / n, 4),
            "clean_conclusion": clean,
            "clean_rate": round(clean / n, 4),
            "useful": useful,
            "useful_rate": round(useful / n, 4),
            "partial_credit": partial_credit,
            "partial_credit_rate": round(partial_credit / n, 4),
            "failed": n - surv,
            "injection_missed": not_fired,
            "degraded_reported": degraded_reported,
            "run_status_counts": _counts(r["run_status"] for r in rs),
        }

    def _counts(it):
        d = {}
        for v in it:
            d[str(v)] = d.get(str(v), 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))

    by_mode = {}
    for mode in (["control"] if args.include_control else []) + MODES:
        rs = [r for r in records if r["mode"] == mode]
        if rs:
            by_mode[mode] = agg(rs)

    overall = agg([r for r in records if r["mode"] != "control"])

    by_site = {}
    for site in EVAL_SITES:
        rs = [r for r in records if r["target_site"] == site]
        if rs:
            by_site[site] = agg(rs)

    report = {
        "meta": {
            "n_per_mode": args.n,
            "seed": args.seed,
            "eval_sites": EVAL_SITES,
            "modes": MODES,
            "metric_definitions": {
                "survival_rate": "终态 ∈ {waiting_human, completed, budget_halted, partial} 的比例；"
                                 "任务没被异常打断",
                "clean_rate": "全程无任何维度使用兜底分的比例（结论完全来自模型）",
                "useful_rate": "存活且库里确实落下了维度评分的比例（结论可用）",
                "partial_credit_rate": "任务失败但库里仍留有已评估维度的比例（沉没成本）",
            },
        },
        "overall": overall,
        "by_mode": by_mode,
        "by_site": by_site,
        "records": records,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"{'模式':<20}{'n':>4}{'存活':>8}{'存活率':>10}{'干净率':>10}"
          f"{'有结论率':>10}{'部分有用':>10}")
    for mode, a in by_mode.items():
        print(f"{mode:<20}{a['n']:>4}{a['survived']:>8}{a['survival_rate']:>10.3f}"
              f"{a['clean_rate']:>10.3f}{a['useful_rate']:>10.3f}"
              f"{a['partial_credit_rate']:>10.3f}")
    print(f"\n整体 n={overall['n']} 存活率={overall['survival_rate']:.3f} "
          f"干净率={overall['clean_rate']:.3f} 有结论率={overall['useful_rate']:.3f} "
          f"部分有用率={overall['partial_credit_rate']:.3f}")
    print(f"注入未命中：{overall['injection_missed']}（应为 0，否则 harness 失效）")
    print(f"报告已写出：{out_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
