"""知识库 chunk 策略回归评测（离线，零 API 成本，可进日常 CI）。

## 定位

`tests/eval/run_eval.py` 管的是 LLM 输出质量，管不到切分策略。本运行器管另一件事：
**改 chunk_size / chunk_overlap、或换 embedding 模型之后，检索结果有没有退步。**

这两件事必须分开，因为变量不同、代价不同、判定方式也不同：
- 招聘评分链路只吃 chat 模型，**完全不调用 embedding**；
- 知识库检索才吃 chunk 策略与 embedding 模型。

所以"换模型"要分别测：换 chat 模型用 run_eval.py，换 embedding 模型用本运行器。

## 为什么必须 sweep，而不是只测当前参数

实测：当前 chunk_size=500 时，6 篇制度文档（309~460 字）各自正好切出 **1 块**——
切分是空操作，改成 600 或 800 对检索结果毫无影响。只测当前参数等于什么都没测，
"改 chunk 策略跑回归"会得到一个必然全绿的假信号。

sweep 把参数往下压（200/300/400），逼出策略的真实失效边界：哪个 chunk_size
开始把一段话切断、切断后哪条黄金用例先失效。

## 断言什么

1. **切分不变量**（与策略无关，任何参数下都必须成立）
   - 文档全覆盖：每篇源文档至少切出 1 块，没有整篇丢失
   - 长度上界：没有块超过 chunk_size
   - 内容不丢：原文去掉空白后是切块拼接结果的子序列（顺序也不许乱）
   - 元数据完整：每块都带 title / source

2. **检索命中**（每条黄金用例，见 chunk_cases.json）
   - expect_titles：期望命中的文档标题必须出现在 Top-K
   - expect_terms：期望词必须出现在**同一个**块里——LLM 拿到的是检索到的
     块原文，跨块的事实它根本看不见，这是切分策略真正的失效点
   - expect_absent_titles：不该命中的文档不能出现（盯检索精度退化）

## 用法

```bash
# 按当前生效参数跑一遍
python tests/eval/run_chunk_eval.py

# 指定参数
python tests/eval/run_chunk_eval.py --chunk-size 300 --chunk-overlap 50

# 参数 sweep：找出策略的失效边界
python tests/eval/run_chunk_eval.py --sweep

# 导出 JSON 报告
python tests/eval/run_chunk_eval.py --out chunk_report.json
```

默认走 BM25 路径（离线、无网络、无成本）。`--vector` 会额外启用向量检索，
需要 embedding 凭据并产生真实 API 调用。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
CASES_FILE = EVAL_DIR / "chunk_cases.json"

# 默认 sweep 网格。刻意包含 (500, 0)/(500, 150) 这类只改 overlap 的组合：
# overlap=0 会让跨块的事实彻底断链，是比改 chunk_size 更容易踩的坑。
DEFAULT_SWEEP: List[Tuple[int, int]] = [
    (200, 50), (300, 50), (400, 50), (500, 50), (800, 50),
    (500, 0), (500, 100), (500, 150),
]


# ================================================================ 环境准备

def _prepare_env() -> None:
    """在任何 src 导入之前把外部依赖切断。

    - DATABASE_URL：src.models.database 在模块加载时即按它建 engine。
      命令行直跑时必须覆盖，否则会往仓库根目录写 recruitment.db——
      评测绝不能碰业务库。作为库被 pytest 导入时**不覆盖**：
      那时 conftest 已经把引擎绑到临时库了，再改环境变量只会造成不一致。
    - LLM/embedding 凭据：评测自己负责摘掉客户端，不依赖调用方配没配 key。
      这是它能进日常 CI 的前提。
    """
    if __name__ == "__main__":
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["DATABASE_URL"] = "sqlite:///" + Path(path).as_posix()
    os.environ.setdefault("SECRET_KEY", "chunk-eval-secret")
    sys.path.insert(0, str(ROOT))


_prepare_env()


@contextlib.contextmanager
def _offline_mode(use_vector: bool, rerank: bool = False):
    """临时摘掉 embedding / chat 客户端、关掉重排，退出时**还原**。

    必须还原：直接赋值会污染同进程内的其他测试——例如 test_kb_persistence
    要验证向量库重建路径，embedding 被永久摘掉后那条路径根本走不到。

    重排默认**关掉**：它要调外部接口，日常 CI 必须零网络调用。
    要测重排就显式加 --rerank。
    """
    from src.config import settings
    from src.rag import knowledge_base as kb

    orig_embeddings = kb._build_embeddings
    orig_chat_llm = kb._build_chat_llm
    orig_rerank = settings.RERANK_ENABLED
    try:
        if not use_vector:
            kb._build_embeddings = lambda: None
        kb._build_chat_llm = lambda: None
        settings.RERANK_ENABLED = bool(rerank)
        yield
    finally:
        kb._build_embeddings = orig_embeddings
        kb._build_chat_llm = orig_chat_llm
        settings.RERANK_ENABLED = orig_rerank


@contextlib.contextmanager
def _temp_persist_dir():
    """评测期间把向量库指向临时目录。

    **评测绝不能碰生产索引**：sweep 会对每组参数 force 重建索引，直接用
    生产目录等于把线上知识库反复删了重建；而且最后留下的是 sweep 最后一个
    参数的索引，与配置不符（虽然指纹能自愈，但没必要冒这个险）。
    """
    from src.rag import knowledge_base as kb

    original = kb.PERSIST_DIR
    tmp = tempfile.mkdtemp(prefix="kb-eval-chroma-")
    kb.PERSIST_DIR = tmp
    try:
        yield
    finally:
        kb.PERSIST_DIR = original
        shutil.rmtree(tmp, ignore_errors=True)


# ================================================================ 切分不变量

def _is_subsequence(small: str, big: str) -> bool:
    """small 是否为 big 的子序列（保序）。用于校验切块没有丢内容、也没有打乱顺序。"""
    it = iter(big)
    return all(ch in it for ch in small)


def _strip_ws(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace())


def check_split_invariants(docs: List[Any], cfg: Dict[str, int]) -> List[Dict[str, Any]]:
    """与策略无关的切分不变量。任何参数下都必须成立，否则就是切分逻辑坏了。"""
    from src.rag import knowledge_base as kb

    checks: List[Dict[str, Any]] = []

    def _add(name: str, ok: bool, detail: str) -> None:
        checks.append({"kind": name, "ok": bool(ok), "detail": detail})

    # 1. 文档全覆盖：每篇源文档至少切出 1 块
    by_title: Dict[str, List[Any]] = {}
    for d in docs:
        by_title.setdefault(d.metadata.get("title", ""), []).append(d)
    missing_docs = [d["title"] for d in kb._all_documents() if d["title"] not in by_title]
    _add("doc_coverage", not missing_docs,
         "全部源文档均有切块" if not missing_docs else f"整篇丢失: {missing_docs}")

    # 2. 长度上界：没有块超过 chunk_size
    oversize = [f"{d.metadata.get('title')}#{len(d.page_content)}"
                for d in docs if len(d.page_content) > cfg["chunk_size"]]
    _add("size_bound", not oversize,
         f"无超长块（上限 {cfg['chunk_size']}）" if not oversize else f"超长块: {oversize[:5]}")

    # 3. 内容不丢 + 顺序不乱：原文（去空白）是切块拼接（去空白）的子序列
    joined = _strip_ws("".join(d.page_content for d in docs))
    lost = [d["title"] for d in kb._all_documents()
            if not _is_subsequence(_strip_ws(d["content"]), joined)]
    _add("content_integrity", not lost,
         "所有源文档内容完整保留且顺序不变" if not lost else f"内容丢失/乱序: {lost}")

    # 4. 元数据完整：每块都带 title / source
    bad_meta = [d.metadata.get("title", "") for d in docs
                if not d.metadata.get("title") or not d.metadata.get("source")]
    _add("metadata", not bad_meta,
         "每块均带 title/source" if not bad_meta else f"元数据缺失: {bad_meta[:5]}")

    return checks


# ================================================================ 检索用例

def _retrieve(query: str, k: int = 4) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """检索并取回本次检索的统计信息。

    **为什么必须把 stats 带出来**：统计里的 `rerank_applied` 是
    「重排确实发生了」的唯一可信证据。命令行传了 --rerank 只代表**请求了**重排；
    凭据缺失时重排客户端直接返回 None 且**不报错**——重排被静默跳过，
    报告却和「重排跑通了」长得一模一样。评测必须能区分这两件事。
    """
    from src.rag import knowledge_base as kb

    docs, stats = kb._retrieve_with_stats(query, k=k)
    hits = [{"title": d.metadata.get("title", ""), "content": d.page_content}
            for d in docs]
    return hits, stats


def run_case(case: Dict[str, Any], k: int = 4) -> Dict[str, Any]:
    """跑一条检索用例：期望标题命中、期望词同块、不该命中的不命中。"""
    hits, stats = _retrieve(case["query"], k=k)
    titles = [h["title"] for h in hits]
    failures: List[str] = []

    expect = case.get("expect_titles") or []
    missing = [t for t in expect if t not in titles]
    if missing:
        failures.append(f"未命中期望文档: {missing}（实际 Top-{k}: {titles}）")

    # 名次：期望文档排第几（1 起）。用于算 MRR / recall@k。
    # **只记录不判定**——排第 1 和排第 2 都是"命中"，把名次写成断言
    # 会让评测对排序抖动过度敏感，而排序抖动在大模型参与时本就存在。
    rank: Optional[int] = None
    for i, t in enumerate(titles, start=1):
        if t in expect:
            rank = i
            break

    terms = case.get("expect_terms") or []
    if terms:
        # 关键：所有词必须落在**同一个**块里。LLM 只看得到被检索到的块原文，
        # 跨块的事实它无从得知——这正是切分策略真正会坏掉的地方。
        co_hit = None
        for h in hits:
            if all(t in h["content"] for t in terms):
                co_hit = h["title"]
                break
        if co_hit is None:
            per_chunk = [{"title": h["title"],
                          "missing": [t for t in terms if t not in h["content"]]}
                         for h in hits]
            failures.append(f"期望词未在同一个块中共现: {terms}（逐块缺失情况: {per_chunk}）")

    absent = case.get("expect_absent_titles") or []
    wrongly = [t for t in absent if t in titles]
    if wrongly:
        failures.append(f"误召回不该命中的文档: {wrongly}（实际 Top-{k}: {titles}）")

    # 语料级负例：断言这些词在检索到的块里一个都不该出现。
    # 用途是"知识库里没有某主题"这件事被钉住——有人往知识库粘进无关制度时立刻报警。
    # 不断言"不该召回某文档"：BM25Retriever 无分数阈值，全零分查询照样回 Top-K，
    # 那种断言恒假，只会成为误报源。
    absent_terms = case.get("expect_absent_terms") or []
    if absent_terms:
        leaked = sorted({t for h in hits for t in absent_terms if t in h["content"]})
        if leaked:
            failures.append(f"语料中本不该出现的词却出现在检索结果里: {leaked}")

    return {
        "name": case.get("name", ""),
        "query": case["query"],
        "passed": not failures,
        "failures": failures,
        "hit_titles": titles,
        "rank": rank,
        "rerank_applied": bool(stats.get("rerank_applied")),
        "candidate_pool": stats.get("candidate_pool"),
    }


# ================================================================ 套件执行

def run_suite(chunk_size: Optional[int] = None,
              chunk_overlap: Optional[int] = None,
              use_vector: bool = False,
              cases: Optional[List[Dict[str, Any]]] = None,
              k: int = 4,
              rerank: bool = False) -> Dict[str, Any]:
    """按给定切分参数跑一遍完整套件，返回结构化结果。"""
    from src.rag import knowledge_base as kb

    if cases is None:
        cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]

    # requires=vector 的用例只在启用向量检索时评估：它们的查询刻意做成
    # 词面重叠很低的语义改写，BM25 路径本就召不回，混进离线门禁只会
    # 天天误报。它们要单独跑 --vector。
    active = [c for c in cases
              if not (c.get("requires") == "vector" and not use_vector)]
    skipped = [c.get("name", "") for c in cases if c not in active]

    cfg = kb.chunk_config(chunk_size, chunk_overlap)

    with _offline_mode(use_vector, rerank), _temp_persist_dir():
        kb.init_knowledge_base(force=True,
                               chunk_size=cfg["chunk_size"],
                               chunk_overlap=cfg["chunk_overlap"])

        split_docs = kb._split_docs(cfg["chunk_size"], cfg["chunk_overlap"])
        invariants = check_split_invariants(split_docs, cfg)
        stats = kb.index_stats(split_docs)
        results = [run_case(c, k=k) for c in active]

    failed = sum(1 for r in results if not r["passed"])
    inv_failed = sum(1 for c in invariants if not c["ok"])

    # 请求了重排就必须**证明**重排真的发生了。
    # 静默跳过是这里最危险的失败模式：凭据缺失时重排客户端直接返回 None，
    # 检索照常返回结果、断言照常全绿，报告读起来却像"重排验证通过"。
    # 所以这一条是硬判据——证据不足就判失败，不给"看起来通过"留余地。
    rerank_applied_count = sum(1 for r in results if r.get("rerank_applied"))
    rerank_check: Dict[str, Any] = {
        "requested": bool(rerank),
        "applied_cases": rerank_applied_count,
        "total_cases": len(results),
    }
    rerank_broken = False
    if rerank and results and rerank_applied_count < len(results):
        rerank_broken = True
        missing = [r["name"] for r in results if not r.get("rerank_applied")]
        rerank_check["error"] = (
            f"请求了重排，但 {len(missing)}/{len(results)} 条用例的重排没有生效"
            f"（未生效的用例: {missing[:5]}）。"
            "最常见原因：RERANK_API_KEY / RERANK_API_BASE 未配置——"
            "空串会让重排客户端直接返回 None 且不报错。"
        )

    # 排序质量指标：**只记录不判定**。
    # MRR / recall@k 用来回答"排得靠不靠前"，而不是"有没有命中"——
    # 命中与否已经是硬判据，排序属于软信号，写成断言会被抖动误伤。
    ranked = [r for r in results if r.get("rank")]
    metrics = {
        "mrr": round(sum(1.0 / r["rank"] for r in ranked) / len(ranked), 4) if ranked else 0.0,
        "recall_at_k": round(len(ranked) / len(results), 4) if results else 0.0,
        "top1_accuracy": round(
            sum(1 for r in results if r.get("rank") == 1) / len(results), 4) if results else 0.0,
        "ranked_cases": len(ranked),
    }

    return {
        "chunk_config": cfg,
        "vector_enabled": bool(use_vector),
        "top_k": k,
        "invariants": invariants,
        "index_stats": stats,
        "results": results,
        "skipped_cases": skipped,
        "metrics": metrics,
        "rerank_check": rerank_check,
        "cases": len(results),
        "passed": len(results) - failed,
        "failed": failed,
        "invariant_failures": inv_failed,
        "ok": failed == 0 and inv_failed == 0 and not rerank_broken,
    }


def run_sweep(use_vector: bool = False,
              cases: Optional[List[Dict[str, Any]]] = None,
              k: int = 4,
              rerank: bool = False) -> Dict[str, Any]:
    """在参数网格上跑同一套黄金用例，找出策略的失效边界。

    关注点不是"哪个配置分最高"，而是**从哪个参数开始有用例失效**——
    那就是当前策略的安全边界。
    """
    runs = []
    for cs, ov in DEFAULT_SWEEP:
        r = run_suite(cs, ov, use_vector=use_vector, cases=cases, k=k, rerank=rerank)
        failed_names = [x["name"] for x in r["results"] if not x["passed"]]
        runs.append({
            "chunk_size": cs, "chunk_overlap": ov,
            "ok": r["ok"], "passed": r["passed"], "failed": r["failed"],
            "invariant_failures": r["invariant_failures"],
            "failed_cases": failed_names,
        })
    return {"runs": runs, "ok": all(x["ok"] for x in runs)}


# ================================================================ 报告

def print_report(report: Dict[str, Any]) -> None:
    cfg = report["chunk_config"]
    print("\n" + "=" * 74)
    print(f"chunk 策略回归：{report['passed']}/{report['cases']} 用例通过"
          f"（chunk_size={cfg['chunk_size']} chunk_overlap={cfg['chunk_overlap']}）")
    print("=" * 74)

    print("\n切分不变量：")
    for c in report["invariants"]:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['kind']}: {c['detail']}")

    st = report.get("index_stats") or {}
    if st:
        print(f"\n索引统计：{st.get('documents')} 篇文档 / {st.get('chunks')} 块"
              f" / 均长 {st.get('avg_chunk_len')} 字"
              f"（最短 {st.get('min_chunk_len')}，最长 {st.get('max_chunk_len')}）"
              f" / 空块 {st.get('empty_chunks')} / 重复块 {st.get('duplicate_chunks')}")

    print("\n检索用例：")
    for r in report["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        rank = r.get("rank")
        rank_txt = f"（名次 {rank}）" if rank else ""
        print(f"\n  [{mark}] {r['name']}{rank_txt}")
        print(f"    query: {r['query']}")
        print(f"    命中: {r['hit_titles']}")
        for f in r["failures"]:
            print(f"      ✗ {f}")

    if report.get("skipped_cases"):
        print(f"\n已跳过（仅向量检索时评估）：{report['skipped_cases']}")

    rc = report.get("rerank_check") or {}
    if rc.get("requested"):
        if rc.get("error"):
            print(f"\n重排校验：FAIL — {rc['error']}")
        else:
            print(f"\n重排校验：PASS"
                  f"（{rc.get('applied_cases')}/{rc.get('total_cases')} "
                  f"条用例的重排确实生效）")

    m = report.get("metrics") or {}
    if m:
        print("\n排序质量（只记录，不参与判定）：")
        print(f"  MRR={m.get('mrr')}  recall@{report.get('top_k')}={m.get('recall_at_k')}"
              f"  Top-1 准确率={m.get('top1_accuracy')}")


def print_sweep(sweep: Dict[str, Any]) -> None:
    print("\n" + "=" * 74)
    print("参数 sweep（同一套黄金用例在不同切分参数下的表现）")
    print("=" * 74)
    print(f"{'chunk_size':>12} {'overlap':>8}  {'结果':<6} {'失败用例'}")
    for r in sweep["runs"]:
        status = "PASS" if r["ok"] else "FAIL"
        names = ", ".join(r["failed_cases"]) or "-"
        print(f"{r['chunk_size']:>12} {r['chunk_overlap']:>8}  {status:<6} {names}")


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库 chunk 策略回归评测（离线）")
    ap.add_argument("--chunk-size", type=int, help="覆盖 chunk_size（默认取全局配置）")
    ap.add_argument("--chunk-overlap", type=int, help="覆盖 chunk_overlap")
    ap.add_argument("--sweep", action="store_true", help="在参数网格上跑，找出失效边界")
    ap.add_argument("--vector", action="store_true",
                    help="同时启用向量检索（需 embedding 凭据，产生真实 API 调用）")
    ap.add_argument("--rerank", action="store_true",
                    help="启用重排（需 rerank 凭据，产生真实 API 调用；默认关闭以保证 CI 零网络）")
    ap.add_argument("--top-k", type=int, default=4, help="检索取前 K 条")
    ap.add_argument("--cases", nargs="*", help="只跑指定用例名（子串匹配）")
    ap.add_argument("--out", help="报告输出路径（JSON）")
    args = ap.parse_args()

    if not CASES_FILE.exists():
        print(f"用例文件不存在: {CASES_FILE}")
        return 2

    all_cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    cases = all_cases
    if args.cases:
        cases = [c for c in all_cases if any(k in c["name"] for k in args.cases)]
    if not cases:
        print("没有匹配的用例")
        return 2

    print(f"准备运行 {len(cases)} 条检索用例"
          f"{'（含向量检索，将产生真实 API 调用）' if args.vector else '（离线 BM25，无成本）'}")

    started = time.time()
    report = run_suite(args.chunk_size, args.chunk_overlap,
                       use_vector=args.vector, cases=cases, k=args.top_k,
                       rerank=args.rerank)
    elapsed = time.time() - started
    report["elapsed_seconds"] = round(elapsed, 1)
    print_report(report)

    sweep_result = None
    if args.sweep:
        sweep_result = run_sweep(use_vector=args.vector, cases=cases, k=args.top_k,
                                 rerank=args.rerank)
        print_sweep(sweep_result)
        if not sweep_result["ok"]:
            # sweep 里的 FAIL 不是门禁失败，而是"这个参数组合已经不行"的发现。
            # 它标出当前策略的安全边界，退出码仍只看当前生效参数那一轮。
            print("\n注：sweep 中出现的 FAIL 即为策略的失效边界（属预期发现，不计入退出码）。")

    if args.out:
        payload = {"report": report, "sweep": sweep_result}
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n报告已写入: {args.out}")

    # 退出码只取决于当前生效参数这一轮——sweep 是探索，不进门禁。
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
