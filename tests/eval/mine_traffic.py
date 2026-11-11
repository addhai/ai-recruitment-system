"""生产流量 → 检索黄金集 候选生成器（手动执行）。

## 解决的问题

手写的黄金集会随时间失真：真实用户问的问题，HR 关心的制度细节，
往往和写用例时脑子里想的不一样。而生产流量**已经在库里了**——
`retrieval_logs` 记着每一条真实问句。这份工具把那些流量变成可评估的用例候选。

## 关键设计：标注必须独立于检索

**绝不能拿"当前检索排第一的文档"当标签**——那是自证：检索错了，标签也跟着错，
评测就永远发现不了问题。

这里的标签来源是**纯文本比对**：把问句和每篇源文档做字符 bigram 重叠度比较，
重叠最高的那篇作为**建议标签**。它只依赖文本本身，与 BM25 / 向量 / 重排
任何一环都无关，所以它是对流水线的独立交叉检查。

即便如此，生成的仍是**候选**而不是既成用例：每条都带 `reviewed: false`，
需要人确认后才能并进 `chunk_cases.json`。理由——自动标签对
"问句与文档用词完全不一致"的情况（用户说工资、文档写薪资）无能为力，
这类恰恰是最该有人看的。

## 自动标签的准确率（实测，不是拍脑袋）

拿 16 条真实感问句（口语化、错别字、省略主语、与知识库无关的闲聊）
跑过一遍自动标注，结果：

| 阈值 | 自动标注 | 标对 | 准确率 | 覆盖率 |
| --- | --- | --- | --- | --- |
| 0.08 | 13 | 10 | 76.9% | 81.2% |
| 0.20 | 10 | 9 | 90.0% | 62.5% |
| **0.25** | **7** | **7** | **100%** | **43.8%** |

所以默认阈值取 0.25：**标的时候要标对，标不准的一律交给人。**
理由是"错误的标签比没有标签更糟"——有标签会锚定复核的人，
让人先接受再纠正；没有标签只是多花点力气自己选。

**这个阈值是从 16 条样本上量出来的，不是通用常数**：真实流量积累后
要重新校准（用 `--min-overlap` 临时调，或直接改这里的默认值）。

## 用法

```bash
# 从生产流量生成候选
python tests/eval/mine_traffic.py --limit 50 --out traffic_candidates.json

# 没有生产流量时，用内置模板造一批候选，验证工具链本身
python tests/eval/mine_traffic.py --demo --out traffic_candidates.json
```

需要能读 `retrieval_logs` 表（数据库里的检索埋点）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _prepare_env() -> None:
    """命令行走独立临时库，避免碰到业务库。

    但流量数据在业务库里……所以默认**不覆盖** DATABASE_URL，只在显式
    指定 --db 时才切换。没有流量时用 --demo。
    """
    os.environ.setdefault("SECRET_KEY", "mine-traffic-secret")
    sys.path.insert(0, str(ROOT))


_prepare_env()


def _bigrams(text: str) -> set:
    chars = [c for c in text if not c.isspace()]
    return {"".join(chars[i:i + 2]) for i in range(len(chars) - 1)}


def _overlap(query: str, doc: str) -> float:
    """问句与文档的字符 bigram 重叠度。

    用 bigram 而不是词，是因为中文分词在问句与文档间会错位（同一个词
    切成不同形状），纯词重叠会漏掉真实关联——这正是 knowledge_base
    里加 bigram 的同一个理由。
    """
    q = _bigrams(query)
    if not q:
        return 0.0
    d = _bigrams(doc)
    if not d:
        return 0.0
    return len(q & d) / len(q)


def _label_by_text(query: str) -> Dict[str, Any]:
    """给问句找建议标签：与源文档文本重叠度最高的那篇。

    **独立于检索链路**——这是这个工具唯一能站得住的标注来源。
    注意独立性来自"比的是源文档原文、从不查检索结果"，**不来自关掉 embedding**：
    关 embedding 只会让 current_hits 偏离生产实际（那才是真正的问题）。

    **并列最高就拒标**：两篇制度的文本重叠度一样高，说明从文本上根本分不出
    该归哪一篇。此时随便挑一篇等于凭空造一个标签出来——宁可交给人。
    领先幅度（margin）也一并返回：领先很小意味着"只有一点点文本依据"，
    这种标签同样不该被当成结论。

    实测（16 条真实感问句）：并列情况确实存在（"新人培训多久"在
    员工培训制度与员工入职流程上打平），改成拒标后误标少一条。
    """
    from src.rag import knowledge_base as kb

    scores = sorted(((_overlap(query, doc["content"]), doc["title"])
                     for doc in kb._all_documents()),
                    key=lambda x: (-x[0], x[1]))
    best_score, best_title = scores[0]
    second_score, second_title = scores[1] if len(scores) > 1 else (0.0, None)

    tied = bool(best_score > 0) and second_score >= best_score - 1e-9
    margin = 0.0 if tied else best_score - second_score
    return {
        "title": None if tied else best_title,
        "score": round(best_score, 4),
        "margin": round(margin, 4),
        "runner_up": second_title,
        "tied": tied,
        "tied_titles": [t for s, t in scores if abs(s - best_score) < 1e-9] if tied else [],
    }


def _demo_queries() -> List[str]:
    """没有生产流量时用的模板问句。

    刻意用**与制度原文用词不同**的说法，模拟真实用户：HR 问"能休几天"
    而不是"年假有多少天"。这样才能验证工具链真的能发现词面不匹配的查询。
    """
    return [
        "能休几天假",
        "一年能休多长时间",
        "涨薪水有什么条件",
        "同事之间能打听对方的收入吗",
        "新来的要先接受什么培训",
        "试用期能拿多少",
        "打分标准是怎么分的",
        "面试要面几轮",
        "报销额度是多少",
        "请假要提前几天申请",
    ]


def _traffic_queries(limit: int) -> List[str]:
    """从检索埋点里取问得最多的问句。"""
    try:
        from src.services import retrieval_log
    except Exception as e:
        print(f"读取检索日志失败: {type(e).__name__}: {str(e)[:200]}")
        return []
    try:
        rows = retrieval_log.top_queries(limit=limit)
    except Exception as e:
        print(f"查询检索日志失败: {type(e).__name__}: {str(e)[:200]}")
        return []
    return [q for q, _ in rows]


def _retrieve_titles(query: str, k: int = 4) -> List[str]:
    """当前检索实际召回了什么——写进候选里给人参考。"""
    from src.rag import knowledge_base as kb

    try:
        kb.init_knowledge_base()
        docs = kb._retrieve_docs(query, k=k)
        return [d.metadata.get("title", "") for d in docs]
    except Exception as e:
        print(f"检索失败({type(e).__name__}): {str(e)[:120]}")
        return []


def build_candidates(queries: List[str], min_overlap: float,
                     top_k: int = 4) -> Dict[str, Any]:
    """把问句转成候选用例。"""
    from src.rag import knowledge_base as kb

    # 只关 LLM：标注不需要它，而 current_hits 必须反映**生产实际的检索路径**
    # （混合 + 重排）。之前连 embedding 一起关了，于是 current_hits 只是
    # BM25 的结果，与线上真正返回的内容不一致——人拿它当参考会看错。
    # 标注的独立性不依赖关 embedding：_label_by_text 只比源文档原文。
    kb._build_chat_llm = lambda: None
    kb._init_attempted = False
    kb.init_knowledge_base()

    candidates: List[Dict[str, Any]] = []
    unlabelable: List[str] = []
    for q in queries:
        lab = _label_by_text(q)
        title, score = lab["title"], lab["score"]
        hits = _retrieve_titles(q, k=top_k)
        if lab["tied"]:
            # 多篇制度文本重叠度打平 → 从文本上分不出来，交给人
            unlabelable.append(q)
            candidates.append({
                "name": f"traffic-{len(candidates) + 1:03d}",
                "query": q,
                "expect_titles": [],
                "reviewed": False,
                "note": (f"文本重叠在 {lab['tied_titles']} 之间打平（{score}），"
                         "无法从文本判断归属，**需人工指定期望文档**"),
                "suggested_title": None,
                "overlap": score,
                "margin": lab["margin"],
                "confidence": "none",
                "current_hits": hits,
            })
            continue
        if not title or score < min_overlap:
            # 文本上也找不到依据 → 自动标注不可靠，留给人判断
            unlabelable.append(q)
            candidates.append({
                "name": f"traffic-{len(candidates) + 1:03d}",
                "query": q,
                "expect_titles": [],
                "reviewed": False,
                "note": f"文本重叠仅 {score}，低于阈值 {min_overlap}，**需人工指定期望文档**",
                "suggested_title": title,
                "overlap": score,
                "margin": lab["margin"],
                "confidence": "none",
                "current_hits": hits,
            })
            continue
        # 置信度：领先幅度太小 = 只有一点点文本依据，不该被当成结论
        confidence = "high" if lab["margin"] >= 0.10 else "low"
        candidates.append({
            "name": f"traffic-{len(candidates) + 1:03d}",
            "query": q,
            "expect_titles": [title],
            "reviewed": False,
            "note": (f"文本重叠 {score}（领先次优 {lab['margin']}）自动建议标签；"
                     + ("**需人工确认**" if confidence == "high"
                        else "**依据很弱，优先人工复核**")),
            "suggested_title": title,
            "overlap": score,
            "margin": lab["margin"],
            "confidence": confidence,
            "current_hits": hits,
        })
    return {"candidates": candidates, "unlabelable": unlabelable}


def main() -> int:
    ap = argparse.ArgumentParser(description="从生产流量生成检索黄金集候选")
    ap.add_argument("--limit", type=int, default=50, help="取问得最多的前 N 条")
    ap.add_argument("--demo", action="store_true",
                    help="没有生产流量时用内置模板问句，验证工具链本身")
    ap.add_argument("--min-overlap", type=float, default=0.25,
                    help="低于该文本重叠度的问句视为自动标注不可靠（默认 0.25）")
    ap.add_argument("--out", default="traffic_candidates.json", help="输出路径")
    args = ap.parse_args()

    if args.demo:
        queries = _demo_queries()
        print(f"演示模式：{len(queries)} 条模板问句")
    else:
        queries = _traffic_queries(args.limit)
        print(f"从检索日志取到 {len(queries)} 条问句")
        if not queries:
            print("没有流量数据。可先用 --demo 验证工具链，或等生产环境积累后重跑。")
            return 0

    result = build_candidates(queries, args.min_overlap)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    labeled = sum(1 for c in result["candidates"] if c["expect_titles"])
    high = sum(1 for c in result["candidates"] if c.get("confidence") == "high")
    low = sum(1 for c in result["candidates"] if c.get("confidence") == "low")
    print(f"\n生成候选 {len(result['candidates'])} 条"
          f"（自动标签 {labeled} 条，需人工指定 {len(result['unlabelable'])} 条）")
    print(f"自动标签的置信度：高 {high} 条 / 低 {low} 条"
          f"（低置信度 = 领先幅度不足 0.1，优先人工看）")
    print(f"输出: {out_path}")
    print("\n下一步：人工核对每条的 expect_titles，把 reviewed 改成 true，")
    print("然后并进 tests/eval/chunk_cases.json。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
