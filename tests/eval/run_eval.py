"""LLM 质量回归评测运行器（手动执行）。

**定位：防退化，不是精度验证。**
实测同一份简历跑 4 次，文化契合给出 72/72/58/58——数值本身不稳定，
所以断言只覆盖**硬性不变量**：该出现的字段出现了吗？评语引用 JD 原文了吗？
未设限的维度有没有被当成 0 分？数值（分数/耗时/token/成本）全部写进报告
但不参与通过判定，多个 repeat 之间看分布趋势。

用法：
    python tests/eval/run_eval.py                    # 跑全部用例
    python tests/eval/run_eval.py --cases c1 c2      # 只跑指定用例
    python tests/eval/run_eval.py --repeat 3         # 每条跑 3 次看波动
    python tests/eval/run_eval.py --out report.json  # 导出报告

需要 .env 里配置真实的 LLM / embedding 凭据——本运行器不 mock 模型，
否则测不出真实退化。会产生真实 API 成本，故不进日常 CI。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

EVAL_DIR = Path(__file__).resolve().parent
CASES_FILE = EVAL_DIR / "cases.json"


# ================================================================ 断言实现
# 每个断言只回答一个"硬性不变量是否成立"，返回 (ok, detail)

def _a_key_present(result: Dict, spec: Dict) -> tuple:
    node = spec.get("node")
    data = result.get(node) or {}
    missing = [k for k in spec.get("keys", []) if not data.get(k)]
    return (not missing), f"缺少键: {missing}" if missing else "字段齐全"


def _a_education_not_empty(result: Dict, spec: Dict) -> tuple:
    """简历明确写了学历却解析为空 → parse_resume 被跳过（当初 raw_text 误判那个 bug）"""
    edu = (result.get("parse_resume") or {}).get("education")
    ok = bool(edu)
    return ok, f"education={str(edu)[:80]}"


def _a_type_is_list(result: Dict, spec: Dict) -> tuple:
    node = spec.get("node") or ""
    value = (result.get(node) or {}).get(spec.get("field"))
    return isinstance(value, list), f"{spec.get('field')} 类型={type(value).__name__}"


def _a_type_is_number(result: Dict, spec: Dict) -> tuple:
    node = spec.get("node") or ""
    value = (result.get(node) or {}).get(spec.get("field"))
    return isinstance(value, (int, float)), f"{spec.get('field')} 类型={type(value).__name__}"


def _a_culture_cites_jd(result: Dict, spec: Dict) -> tuple:
    """JD 写了价值观时，文化评语必须与其中至少一项有实质关联。

    JD 里的 culture_values 常常是整句长描述（如"相比工作经验，我们更关注动手能力"），
    要求评语逐字复述不现实。这里改为**关键词级匹配**：从每个价值观里抽出
    2-4 字的核心词，命中任一即为通过——真正要拦的是"退回了写死的通用价值观"
    （那套是创新精神/团队协作/客户导向），与 JD 原文特有措辞毫无交集。
    """
    values = (result.get("_jd_culture_values") or [])
    if not values:
        return True, "该 JD 未设价值观，跳过（不参与评分）"

    node = result.get("assess_cultural_fit") or {}
    comment = str(node.get("reasons") or "") + str(node.get("evidence") or "")
    if not comment.strip():
        return False, "文化评语为空——可能走了兜底分"

    # 从价值观文本里抽核心词：中文取连续片段，英文取词
    import re
    hit = []
    for v in values:
        text = str(v)
        # 抽取 3 字以上的中文片段与 4 字母以上的英文词作为候选关键词
        tokens = re.findall(r"[\u4e00-\u9fa5]{3,}", text) + \
                 re.findall(r"[A-Za-z]{4,}", text)
        # 长句子按标点切成短块，取每块前若干字更贴近评语表述
        for seg in re.split(r"[，,。；;：:、\s]+", text):
            seg = seg.strip()
            if len(seg) >= 3:
                tokens.append(seg[:4])
                tokens.append(seg[-4:] if len(seg) > 4 else seg)
        matched = [t for t in set(tokens) if t and t in comment]
        if matched:
            hit.append((text[:20], matched[:2]))

    if hit:
        return True, f"命中价值观关键词: {hit[:2]}"
    return False, (f"评语与 JD 原文价值观无实质关联（疑似退回通用价值观）。"
                   f"JD 价值观首项: {str(values[0])[:40]}；评语片段: {comment[:80]}")


def _a_missing_skills_shape(result: Dict, spec: Dict) -> tuple:
    node = (result.get("evaluate_skill_match") or {})
    matched = node.get("matched_skills")
    missing = node.get("missing_skills")
    ok = isinstance(matched, list) and isinstance(missing, list)
    return ok, f"matched={len(matched or [])} missing={len(missing or [])}"


def _a_no_zero_risk_for_unconstrained(result: Dict, spec: Dict) -> tuple:
    """未设限的维度不得在决策理由/风险里被当成 0 分——那个 bug 修过，必须防回归"""
    free = result.get("_unconstrained_dimensions") or []
    if not free:
        return True, "无未设限维度"
    blob = json.dumps(result.get("generate_hiring_decision") or {}, ensure_ascii=False)
    offenders = []
    label_map = {"education_score": "教育", "culture_match_score": "文化",
                 "experience_score": "经验", "questionnaire_score": "问卷",
                 "skill_match_score": "技能"}
    for dim in free:
        label = label_map.get(dim, dim)
        for pattern in (f"{label}匹配为0分", f"{label}契合为0分", f"{label}匹配0分",
                        f"{label}为0分", f"{label}0分"):
            if pattern in blob:
                offenders.append(pattern)
    return (not offenders), f"发现虚假 0 分表述: {offenders}" if offenders else "无虚假 0 分"


def _a_decision_shape(result: Dict, spec: Dict) -> tuple:
    allowed = {"推荐录用", "待定", "不推荐", "待人工复核"}
    decision = (result.get("generate_hiring_decision") or {}).get("decision")
    if decision is None:
        return False, "未产出决策"
    ok = any(a in str(decision) for a in allowed)
    return ok, f"decision={decision}"


def _load_faithfulness():
    """按文件路径加载忠实度模块。

    直接 `import tests.eval.faithfulness` 在以脚本方式运行时可能不可用
    （tests/eval 下没有 __init__.py），按路径加载最稳，两种入口都能用。

    **必须先把模块注册进 sys.modules 再执行**：@dataclass 在类定义完成时
    会回查 `sys.modules[cls.__module__]`，而动态加载出来的模块默认不在
    sys.modules 里 → 回查拿到 None → 抛
    `AttributeError: 'NoneType' object has no attribute '__dict__'`。
    那个异常发生在**导入期**，于是整个忠实度断言模块根本加载不起来，
    每条知识库用例都只报"断言执行异常"——看起来像断言不通过，
    实际是断言压根没跑。注册进 sys.modules 是 importlib 动态加载的标准做法。
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "faithfulness.py"
    spec = importlib.util.spec_from_file_location("kb_faithfulness", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _a_faithful(result: Dict, spec: Dict) -> tuple:
    """回答里的事实必须能在检索到的片段里找到出处。

    判定逻辑（数字硬断言 / 名称宽松 / 自由文本只记录）都在 faithfulness.py 里，
    这里只负责把结果转成 run_eval 的 (ok, detail)。
    """
    answer = result.get("answer") or ""
    contexts = result.get("_kb_contexts") or []
    res = _load_faithfulness().check_faithfulness(
        answer, contexts, strict=bool(spec.get("strict")))
    if res["ok"]:
        return True, res["detail"]
    tokens = [u.get("token") for u in res.get("unsupported", [])]
    return False, f"无依据事实 {tokens}；{res['detail']}"


ASSERTIONS: Dict[str, Callable[[Dict, Dict], tuple]] = {
    "key_present": _a_key_present,
    "education_not_empty": _a_education_not_empty,
    "type_is_list": _a_type_is_list,
    "type_is_number": _a_type_is_number,
    "culture_cites_jd": _a_culture_cites_jd,
    "missing_skills_shape": _a_missing_skills_shape,
    "no_zero_risk_for_unconstrained": _a_no_zero_risk_for_unconstrained,
    "decision_shape": _a_decision_shape,
    "faithful": _a_faithful,
}


# ================================================================ 用例执行

def _run_knowledge_case(case: Dict, repeat: int = 1) -> Dict[str, Any]:
    """知识库问答用例：真实检索 + 真实 LLM，再断言忠实度。

    **为什么不直接复用 `_query_sync` 的返回值**：它返回的 `sources` 里
    `content` 被截断到 200 字，而 LLM 实际看到的是**完整 chunk**
    （`_format_docs` 不截断）。拿截断片段做忠实度比对会凭空产生误报——
    模型引用了 200 字之后的内容，比对素材里却没有。

    所以这里自己走一遍「检索 → 拼上下文 → 调 LLM」，保证**上下文、答案、
    比对素材三者来自同一次检索**。

    成本与 L1 同性质：每次都要真实调 LLM，手动触发，不进日常 CI。
    """
    from src.rag import knowledge_base as kb

    kb.init_knowledge_base()
    docs = kb._retrieve_docs(case["query"], k=case.get("top_k", 4))
    contexts = [{"title": d.metadata.get("title", ""), "content": d.page_content}
                for d in docs]
    context_text = kb._format_docs(docs)

    runs: List[Dict[str, Any]] = []
    failures: List[str] = []

    for i in range(repeat):
        try:
            answer = kb._invoke_qa_with_instrumentation(context_text, case["query"])
        except Exception as e:
            detail = f"{type(e).__name__}: {str(e)[:200]}"
            failures.append(f"[run {i+1}] 知识库问答失败: {detail}")
            runs.append({"run": i + 1, "metrics": {"error": detail}, "assertions": []})
            continue

        result = {"answer": answer, "_kb_contexts": contexts}
        check = _check_assertions(case, result)
        metrics = dict(check["metrics"])
        metrics.update({
            "sources": len(contexts),
            "context_chars": len(context_text),
            "answer_chars": len(answer),
            "hit_titles": [c["title"] for c in contexts],
        })
        runs.append({"run": i + 1, "metrics": metrics,
                     "assertions": check["assertions"]})
        for f in check["failures"]:
            tag = f"[run {i+1}] {f}"
            if tag not in failures:
                failures.append(tag)

    return {"name": case["name"], "passed": not failures,
            "runs": runs, "failures": failures}


def run_case(case: Dict, repeat: int = 1) -> Dict[str, Any]:
    """跑一条用例，返回 {name, passed, runs: [...], failures: [...]}"""
    # 知识库用例（kind=knowledge）走另一条链路：真实检索 + 真实 LLM，
    # 断言答案忠实度。招聘评分用例的逻辑不变。
    if case.get("kind") == "knowledge":
        return _run_knowledge_case(case, repeat)

    from src.services import job_parser
    from src.workflow import recruitment_graph as rg

    jd_path = EVAL_DIR / case["jd_ref"]
    jd_text = jd_path.read_text(encoding="utf-8")

    # JD 解析（真实调用，带埋点）
    parsed = None
    for attempt in range(2):
        try:
            import asyncio
            parsed, err = asyncio.run(job_parser.parse_job_description(jd_text))
        except Exception as e:
            parsed, err = None, str(e)
        if parsed:
            break
    if not parsed:
        return {"name": case["name"], "passed": False,
                "runs": [], "failures": [f"JD 解析失败: {err}"]}

    runs: List[Dict[str, Any]] = []
    failures: List[str] = []

    for i in range(repeat):
        result = _execute_once(case, parsed)
        check = _check_assertions(case, result)
        runs.append({"run": i + 1, "metrics": check["metrics"],
                     "assertions": check["assertions"]})
        for f in check["failures"]:
            tag = f"[run {i+1}] {f}"
            if tag not in failures:
                failures.append(tag)

    return {"name": case["name"], "passed": not failures,
            "runs": runs, "failures": failures}


def _execute_once(case: Dict, jd_profile: Dict) -> Dict[str, Any]:
    """单次执行：直接驱动评估节点（不经 LangGraph 编排，聚焦节点输出本身）"""
    import asyncio
    from src.workflow import recruitment_graph as rg
    from src.workflow import db_actions as da

    resume_ref = case.get("resume_ref")
    if resume_ref:
        from src.services.resume_cleaner import parse_resume_with_fallback
        pdf = EVAL_DIR / resume_ref
        text, _src, _ocr = parse_resume_with_fallback(str(pdf), pdf.name, pdf.read_bytes())
    else:
        text = case.get("resume_text") or ""

    # 建临时候选人承载节点副作用
    cid = _mk_candidate(f"评测-{case['name']}")
    da.save_resume_parsed(cid, {"raw_text": text})

    state = {
        "candidate_id": cid,
        "candidate_name": f"评测-{case['name']}",
        "resume_text": text,
        "position": case.get("position", ""),
        "jd_profile": jd_profile,
        "jd_source": "job_description",
    }

    out: Dict[str, Any] = {}

    async def _run_nodes():
        s = state
        s = await rg.parse_resume(s)
        out["parse_resume"] = s.get("parsed_resume") or {}
        s = await rg.extract_skills(s)
        s = await rg.evaluate_skill_match(s)
        out["evaluate_skill_match"] = s.get("skill_match_details") or {}
        s = await rg.evaluate_experience(s)
        out["evaluate_experience"] = {"score": s.get("experience_score")}
        s = await rg.evaluate_education(s)
        out["evaluate_education"] = {"score": s.get("education_score")}
        s = await rg.assess_cultural_fit(s)
        out["assess_cultural_fit"] = s.get("culture_match_details") or {}
        s["interview_scores"] = [{"round": 1, "score": 86}]
        s = await rg.generate_hiring_decision(s)
        out["generate_hiring_decision"] = s.get("final_recommendation") or {}
        return s

    try:
        final = asyncio.run(_run_nodes())
    except Exception as e:
        out["_error"] = str(e)[:300]
        final = state

    out["_jd_culture_values"] = (jd_profile.get("culture_values") or [])
    out["_unconstrained_dimensions"] = final.get("unconstrained_dimensions") or []
    out["_scores"] = {
        "skill": final.get("skill_match_score"),
        "experience": final.get("experience_score"),
        "education": final.get("education_score"),
        "culture": final.get("culture_match_score"),
        "overall": final.get("overall_score"),
        "decision": final.get("final_decision"),
    }
    _cleanup_candidate(cid)
    return out


def _check_assertions(case: Dict, result: Dict) -> Dict[str, Any]:
    assertions_out = []
    failures = []
    for spec in case.get("assertions", []):
        kind = spec.get("kind")
        fn = ASSERTIONS.get(kind)
        if fn is None:
            failures.append(f"未知断言类型: {kind}")
            continue
        try:
            ok, detail = fn(result, spec)
        except Exception as e:
            ok, detail = False, f"断言执行异常: {e}"
        assertions_out.append({"kind": kind, "ok": ok, "detail": detail})
        if not ok:
            failures.append(f"{kind}: {detail}")

    # 数值只记录，不参与判定
    metrics = dict(result.get("_scores") or {})
    if result.get("_error"):
        metrics["error"] = result["_error"]
    return {"metrics": metrics, "assertions": assertions_out, "failures": failures}


def _mk_candidate(name: str) -> int:
    import uuid
    from src.models.database import SessionLocal, Candidate
    with SessionLocal() as db:
        c = Candidate(name=name, email=f"eval_{uuid.uuid4().hex[:10]}@eval.local")
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id


def _cleanup_candidate(cid: int) -> None:
    from src.models.database import SessionLocal, Candidate
    with SessionLocal() as db:
        c = db.query(Candidate).filter(Candidate.id == cid).first()
        if c:
            db.delete(c)
            db.commit()


# ================================================================ 口径指纹

def _provenance() -> Dict[str, Any]:
    """记录本次评测的**口径**——决定"两次结果能不能直接比"的那几项。

    这是"换模型做回归"里最容易踩的坑：新旧分数的差异，到底来自模型变好，
    还是来自提示词/权重也一起改了？两者混在一起就无从判断。
    所以这里把口径拆成三块分别记录：

    - code_digest：决定评分的模块源码摘要（提示词、权重、门限）。
      它变了 → 评分口径变了，与模型无关。
    - config_digest：运行时配置摘要（模型名/base_url/temperature/timeout）。
      **换模型时它必然变**——这是预期内的变化，不是故障。
    - kb_chunk / embedding_model：知识库侧口径。本运行器不跑知识库问答，
      但仍记录下来：它们变了会影响检索，而检索不进这条链路，
      记录下来是为了让人知道"这次变的到底是什么"。

    三块分别给出，才能区分"我换了模型"与"我不知怎么连提示词也改了"。
    """
    from src.config import settings
    from src.rag import knowledge_base as kb
    from src.services import llm_config
    from src.workflow import scoring_fingerprint

    cfg = llm_config.get_effective_config()
    return {
        "fingerprint": scoring_fingerprint.fingerprint(),
        "code_digest": scoring_fingerprint.code_digest(scoring_fingerprint.module_sources()),
        "config_digest": scoring_fingerprint.config_digest(cfg),
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "temperature": cfg.get("temperature"),
        "timeout_seconds": cfg.get("timeout_seconds"),
        "config_source": cfg.get("source"),
        "embedding_model": settings.EMBEDDING_MODEL,
        "kb_chunk": kb.chunk_config(),
    }


# ================================================================ 基线对比

def _case_assertion_status(case_result: Dict) -> Dict[str, bool]:
    """把一条用例的断言状态压成 {kind: ok}。

    多次 repeat 时取"全部通过"才算 ok——只要有一次不通过，这条用例就没通过。
    """
    out: Dict[str, bool] = {}
    for run in case_result.get("runs", []):
        for a in run.get("assertions", []):
            kind = a.get("kind")
            out[kind] = out.get(kind, True) and bool(a.get("ok"))
    return out


def compare_with_baseline(current: Dict, baseline: Dict,
                          drift_threshold: float = 10.0) -> Dict[str, Any]:
    """与基线报告对比，标出退步。

    判定分两类，性质不同：

    1. **断言退步（硬）**：某条硬性不变量从 PASS 变 FAIL。这一定是退步——
       字段缺失、类型漂移、评语没引用 JD 原文、虚假 0 分——任何模型下都不该发生。
    2. **分数漂移（软）**：同一维度的中位数相对基线移动超过阈值。
       **不计入退出码**：大模型采样本身不可复现（实测同一份简历文化契合
       能给出 72/72/58/58），把波动写成断言就是误报源。
       它只用来回答"方向上变了没有"，供人判断。

    口径变化单独列出。换模型时 config_digest 必然变，这是预期内的；
    但 code_digest 也变了就说明提示词/权重被一起动了，两种变化混在一起，
    分数的差异无法归因——这正是要显式告诉你的。
    """
    cur_prov = current.get("provenance") or {}
    base_prov = baseline.get("provenance") or {}

    prov_changes: List[str] = []
    if cur_prov.get("code_digest") != base_prov.get("code_digest"):
        prov_changes.append("评分源码口径已变（提示词/权重/门限）——与模型无关，属额外变化")
    if cur_prov.get("config_digest") != base_prov.get("config_digest"):
        prov_changes.append(f"运行时配置口径已变（模型 {base_prov.get('model')} → {cur_prov.get('model')}）")
    if cur_prov.get("embedding_model") != base_prov.get("embedding_model"):
        prov_changes.append(f"embedding 模型已变（{base_prov.get('embedding_model')} → {cur_prov.get('embedding_model')}）")
    if cur_prov.get("kb_chunk") != base_prov.get("kb_chunk"):
        prov_changes.append(f"知识库切分参数已变（{base_prov.get('kb_chunk')} → {cur_prov.get('kb_chunk')}）")

    cur_cases = {r["name"]: r for r in current.get("results", [])}
    base_cases = {r["name"]: r for r in baseline.get("results", [])}

    regressions: List[Dict[str, Any]] = []
    fixed: List[Dict[str, Any]] = []

    for name, cur in cur_cases.items():
        cur_status = _case_assertion_status(cur)
        base_status = _case_assertion_status(base_cases.get(name, {}))
        for kind, ok in cur_status.items():
            was = base_status.get(kind)
            if was is True and not ok:
                regressions.append({"case": name, "kind": kind,
                                    "detail": "基线通过，本次失败"})
            elif was is False and ok:
                fixed.append({"case": name, "kind": kind, "detail": "基线失败，本次通过"})

    removed = [n for n in base_cases if n not in cur_cases]
    added = [n for n in cur_cases if n not in base_cases]

    # 分数漂移（软）：比较各维度中位数
    drift: List[Dict[str, Any]] = []
    cur_dist = (current.get("summary") or {}).get("score_distribution") or {}
    base_dist = (baseline.get("summary") or {}).get("score_distribution") or {}
    for key in ("skill", "experience", "education", "culture", "overall"):
        c = cur_dist.get(key)
        b = base_dist.get(key)
        if not c or not b:
            continue
        delta = float(c["median"]) - float(b["median"])
        if abs(delta) >= drift_threshold:
            drift.append({"dimension": key, "baseline_median": b["median"],
                          "current_median": c["median"], "delta": round(delta, 1)})

    return {
        "provenance_changes": prov_changes,
        "regressions": regressions,
        "fixed": fixed,
        "added_cases": added,
        "removed_cases": removed,
        "score_drift": drift,
        "drift_threshold": drift_threshold,
        "comparable": not prov_changes,
        "ok": not regressions,
    }


def print_baseline_diff(diff: Dict[str, Any]) -> None:
    print("\n" + "=" * 74)
    print("与基线对比")
    print("=" * 74)

    if diff["provenance_changes"]:
        print("\n口径变化（决定两次结果是否可直接比较）：")
        for c in diff["provenance_changes"]:
            print(f"  • {c}")
        if not diff["comparable"]:
            print("  → 口径已变：分数差异不能直接归因到模型效果，需结合上面的变化项解读")
    else:
        print("\n口径未变（源码/配置/切分参数一致），分数可直接比较")

    print("\n断言退步（硬判据，计入失败）：")
    if diff["regressions"]:
        for r in diff["regressions"]:
            print(f"  ✗ {r['case']} / {r['kind']}: {r['detail']}")
    else:
        print("  无")

    if diff["fixed"]:
        print("\n修复项：")
        for r in diff["fixed"]:
            print(f"  ✓ {r['case']} / {r['kind']}: {r['detail']}")

    print(f"\n分数漂移（软信号，阈值 ±{diff['drift_threshold']}，不计入失败）：")
    if diff["score_drift"]:
        for d in diff["score_drift"]:
            print(f"  {d['dimension']:12} {d['baseline_median']} → {d['current_median']}"
                  f"  ({d['delta']:+})")
    else:
        print("  无")

    if diff["added_cases"] or diff["removed_cases"]:
        print(f"\n用例集变动：新增 {diff['added_cases'] or '无'}；移除 {diff['removed_cases'] or '无'}")


# ================================================================ 报告

def _summarize(results: List[Dict], repeat: int) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    # 数值分布：只做参考，不判定
    dist: Dict[str, Dict[str, Any]] = {}
    for key in ("skill", "experience", "education", "culture", "overall"):
        vals = [run["metrics"].get(key) for r in results for run in r["runs"]
                if isinstance(run["metrics"].get(key), (int, float))]
        if vals:
            dist[key] = {
                "n": len(vals),
                "min": min(vals), "max": max(vals),
                "median": statistics.median(vals),
                "spread": max(vals) - min(vals),
            }

    decisions: Dict[str, int] = {}
    for r in results:
        for run in r["runs"]:
            d = str(run["metrics"].get("decision"))
            decisions[d] = decisions.get(d, 0) + 1

    return {
        "cases": total, "passed": passed, "failed": total - passed,
        "repeat": repeat,
        "score_distribution": dist,
        "decisions": decisions,
    }


def print_report(results: List[Dict], summary: Dict, elapsed: float) -> None:
    print("\n" + "=" * 74)
    print(f"评测结果：{summary['passed']}/{summary['cases']} 用例通过"
          f"（每条跑 {summary['repeat']} 次，耗时 {elapsed:.0f}s）")
    print("=" * 74)

    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"\n[{mark}] {r['name']}")
        for run in r["runs"]:
            m = run["metrics"]
            score_txt = " ".join(f"{k}={m.get(k)}" for k in
                                 ("skill", "experience", "education", "culture", "overall")
                                 if m.get(k) is not None)
            print(f"    run{run['run']}: {score_txt}"
                  + (f"  决策={m.get('decision')}" if m.get("decision") else ""))
            for a in run["assertions"]:
                if not a["ok"]:
                    print(f"      ✗ {a['kind']}: {a['detail']}")
        if r["failures"]:
            print(f"    失败断言: {len(r['failures'])} 项")

    print("\n" + "-" * 74)
    print("数值分布（仅记录，不参与通过判定）：")
    for key, d in summary["score_distribution"].items():
        print(f"  {key:12} n={d['n']:3}  min={d['min']:>5}  median={d['median']:>6}  "
              f"max={d['max']:>5}  波动={d['spread']}")
    print(f"\n决策分布: {summary['decisions']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 质量回归评测（防退化）")
    ap.add_argument("--cases", nargs="*", help="只跑指定用例名（子串匹配）")
    ap.add_argument("--repeat", type=int, default=1, help="每条用例重复次数，看数值波动")
    ap.add_argument("--out", help="报告输出路径（JSON）")
    ap.add_argument("--baseline", help="基线报告 JSON 路径；给出后做退步对比")
    ap.add_argument("--drift-threshold", type=float, default=10.0,
                    help="分数中位数漂移告警阈值（软信号，不计入退出码）")
    args = ap.parse_args()

    if not CASES_FILE.exists():
        print(f"用例文件不存在: {CASES_FILE}")
        return 2

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    if args.cases:
        cases = [c for c in cases if any(k in c["name"] for k in args.cases)]
    if not cases:
        print("没有匹配的用例")
        return 2

    print(f"准备运行 {len(cases)} 条用例，每条 {args.repeat} 次")
    print("注意：将产生真实 LLM API 调用与成本\n")

    started = time.time()
    results = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['name']} ...", flush=True)
        results.append(run_case(case, repeat=args.repeat))

    elapsed = time.time() - started
    summary = _summarize(results, args.repeat)
    print_report(results, summary, elapsed)

    # 口径：这次跑的是哪套提示词 + 哪个模型 + 哪套切分参数。
    # 不记它就无法判断"这次和上次的分数差"该归因给谁。
    provenance = _provenance()
    print(f"\n口径指纹: {provenance.get('fingerprint')}  "
          f"模型={provenance.get('model')}  "
          f"temperature={provenance.get('temperature')}  "
          f"切分={provenance.get('kb_chunk')}")

    report = {"provenance": provenance, "summary": summary, "results": results}

    diff = None
    if args.baseline:
        baseline_path = Path(args.baseline)
        if not baseline_path.exists():
            print(f"基线报告不存在: {baseline_path}")
            return 2
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        diff = compare_with_baseline(report, baseline, args.drift_threshold)
        print_baseline_diff(diff)
        report["baseline_diff"] = diff

    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\n报告已写入: {args.out}")

    # 退出码：断言失败算失败；基线对比里的**断言退步**也算失败。
    # 分数漂移不计入——大模型采样本身不可复现，把它写成门禁只会天天误报。
    if summary["failed"] != 0:
        return 1
    if diff is not None and diff["regressions"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())