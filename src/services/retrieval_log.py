"""知识库检索埋点：写入 + 保留期清理 + 高频问题聚合。

**为什么单独一个模块**：检索埋点的调用点会分散（在线问答、评测跑批、
将来可能出现的离线建库），但口径必须统一——同一个问题在两处算出的
sha256 不一致，"被问得最多的问题"就会裂成两条互相看不见的记录。
所以截断、哈希、字段兜底都收在这里，调用方只负责把现场数据传进来。

**写库失败绝不影响主流程**：检索埋点是旁路可观测性，一次写库异常
不该让用户的问答失败（与 llm_invoke._write_log 同一原则）。
写失败只记一条 warning，主流程照常返回。

**只记元数据不记正文**：召回落的是文档片段正文，可能含员工个人信息；
正文入库既让这张表迅速膨胀，风险也高得多。这里只留标题。

**保留期**：沿用 settings.LLM_LOG_RETENTION_DAYS（默认 90 天）。
检索日志里可能含员工姓名，保留期不宜比 LLM 调用日志更长。
"""
import hashlib
from datetime import datetime, timedelta
from typing import Any, List, Optional, Tuple

from src.config import settings
from src.logging_setup import get_logger

logger = get_logger(__name__)

# 问句长度上限，与 src/api/knowledge_base.py 里 KnowledgeQuery.max_length 一致。
# 接口层已经限了 2000 字，这里再限一次是为了兜住不经接口的入口（脚本 / 评测）。
QUERY_TEXT_MAX_LEN = 2000

# 召回落库的标题条数上限：top_k 通常是 4~8，兜底防止调用方误传整个文档列表
MAX_TITLES = 50

# 检索模式标记（与 knowledge_base 的 _retrieval_mode 产出的取值对齐）
MODE_HYBRID = "hybrid_rag"      # 两路都参与
MODE_BM25 = "bm25_llm"          # 仅 BM25 召回
MODE_VECTOR = "vector_llm"      # 仅向量召回
MODE_FALLBACK = "fallback"       # 检索器都不可用，走模板兜底

# 保留期兜底值：settings 里没有该配置项时用这个（不新增配置项，避免动 src/config.py）
_DEFAULT_RETENTION_DAYS = 90


def _retention_days() -> int:
    """日志保留天数。默认沿用 LLM_LOG_RETENTION_DAYS，取不到时兜底 90 天。"""
    try:
        days = int(getattr(settings, "LLM_LOG_RETENTION_DAYS", _DEFAULT_RETENTION_DAYS)
                   or _DEFAULT_RETENTION_DAYS)
    except (TypeError, ValueError):
        days = _DEFAULT_RETENTION_DAYS
    return days if days > 0 else _DEFAULT_RETENTION_DAYS


def query_hash(query_text: str) -> str:
    """问句的 sha256，用作聚类与去重的键。

    哈希对象是**入库后的问句**（去首尾空白、截断到 2000 字），
    这样"库里这一行的 hash"永远等于 sha256(这一行的 query_text)，
    排查时可以直接验算，不必猜当时截断到了哪里。
    """
    return hashlib.sha256((query_text or "").encode("utf-8")).hexdigest()


def _normalize_query(query_text: Any) -> str:
    """规整问句：去首尾空白 + 长度截断。

    去空白是因为用户多打几个空格不该算成两个问题；
    截断是因为问句可能很长（接口层已限 2000 字，这里兜住脚本入口）。
    """
    text = query_text if isinstance(query_text, str) else ("" if query_text is None else str(query_text))
    return text.strip()[:QUERY_TEXT_MAX_LEN]


def _titles(value: Any) -> Optional[List[str]]:
    """把召回落的结果规整成「标题字符串列表」。

    调用方可能直接传标题字符串，也可能传 langchain 的 Document 对象
    （标题在 metadata["title"] 里）或 dict。统一取标题、丢掉正文。
    返回 None 表示"这一路没有结果"，与"结果为空列表"区分开——
    排查时要能看出是向量路没召回，还是压根没跑向量路。
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    try:
        items = list(value)
    except TypeError:
        return None

    out: List[str] = []
    for item in items:
        title = ""
        if isinstance(item, str):
            title = item
        elif isinstance(item, dict):
            title = str(item.get("title") or "")
        else:
            meta = getattr(item, "metadata", None)
            if isinstance(meta, dict):
                title = str(meta.get("title") or "")
        title = title.strip()[:200]
        if title:
            out.append(title)
        if len(out) >= MAX_TITLES:
            break
    return out


def write_retrieval_log(
    *,
    query_text: str = "",
    top_k: Optional[int] = None,
    bm25_titles: Optional[List[Any]] = None,
    vector_titles: Optional[List[Any]] = None,
    merged_titles: Optional[List[Any]] = None,
    bm25_ms: Optional[float] = None,
    vector_ms: Optional[float] = None,
    total_ms: Optional[float] = None,
    mode: Optional[str] = None,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    embedding_model: Optional[str] = None,
    degraded: bool = False,
    rerank_applied: bool = False,
    rerank_ms: Optional[float] = None,
    **extra: Any,
) -> None:
    """写一条检索埋点。任何失败都只告警，不影响主流程。

    参数名与 RetrievalLog 的列名一一对应，调用方按现场数据直传即可：
    query_text 原始问句；bm25_titles / vector_titles / merged_titles 三路召回标题；
    bm25_ms / vector_ms / total_ms 分段耗时（毫秒）；
    mode 检索模式；chunk_size / chunk_overlap / embedding_model 当时的切分与向量口径；
    degraded 是否降级。

    **未知字段一律忽略**（记 debug 日志）：埋点是旁路，调用方传错字段名
    不能让用户的问答跟着失败——这与"写库失败不影响主流程"是同一条底线。
    """
    try:
        from src.models.database import SessionLocal, RetrievalLog, ensure_tables

        if extra:
            # 不认识就忽略，但要让"字段被悄悄丢掉"这件事在 debug 日志里露出来
            logger.debug("检索日志忽略未知字段", extra={"fields": sorted(extra.keys())})

        # 确保表存在：评测脚本 / CLI 等不经应用启动的入口会缺表，
        # 没有这张表时埋点会静默丢失（与 llm_invoke 的做法一致）
        ensure_tables()

        text = _normalize_query(query_text)
        db = SessionLocal()
        try:
            db.add(RetrievalLog(
                created_at=datetime.utcnow(),
                query_text=text,
                query_hash=query_hash(text),
                top_k=top_k,
                bm25_titles=_titles(bm25_titles),
                vector_titles=_titles(vector_titles),
                merged_titles=_titles(merged_titles),
                bm25_ms=bm25_ms,
                vector_ms=vector_ms,
                total_ms=total_ms,
                mode=(mode or None),
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                embedding_model=embedding_model,
                degraded=bool(degraded),
                rerank_applied=bool(rerank_applied),
                rerank_ms=rerank_ms,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:  # 埋点失败绝不能拖垮业务
        logger.warning("写检索日志失败",
                       extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})


def purge_old_retrieval_logs(days: Optional[int] = None) -> int:
    """清理过期检索日志，返回删除行数。

    日志表会随检索量线性增长，不清理迟早撑爆磁盘；启动时调一次即可。
    days 不传时取 settings.LLM_LOG_RETENTION_DAYS（默认 90 天）。
    清理失败返回 0（与 purge_old_llm_logs 同一语义）：清理是维护动作，
    失败不该把启动流程带崩。
    """
    if days is None:
        days = _retention_days()
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = _retention_days()
    if days <= 0:
        return 0

    from src.models.database import SessionLocal, RetrievalLog, ensure_tables

    cutoff = datetime.utcnow() - timedelta(days=days)
    try:
        ensure_tables()
        db = SessionLocal()
        try:
            count = db.query(RetrievalLog).filter(
                RetrievalLog.created_at < cutoff).delete(synchronize_session=False)
            db.commit()
            if count:
                logger.info(f"清理 {days} 天前的检索日志 {count} 条")
            return count
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        logger.warning("清理检索日志失败",
                       extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})
        return 0


def top_queries(limit: int = 20) -> List[Tuple[str, int]]:
    """按被问次数倒序返回 [(问句, 次数)]，供把生产流量变成评测集。

    聚类键是 query_hash（sha256）：同一问题反复问才算多次，
    不同问法各算各的。次数相同时按问句排序，保证结果稳定可复现。

    查询失败返回空列表：这是给评测/分析用的辅助读接口，
    拿不到数据不应让调用方报错。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    if limit <= 0:
        return []

    from sqlalchemy import func
    from src.models.database import SessionLocal, RetrievalLog, ensure_tables

    try:
        ensure_tables()
        db = SessionLocal()
        try:
            rows = (
                db.query(
                    func.min(RetrievalLog.query_text).label("query_text"),
                    func.count(RetrievalLog.id).label("cnt"),
                )
                .group_by(RetrievalLog.query_hash)
                .order_by(func.count(RetrievalLog.id).desc(),
                          func.min(RetrievalLog.query_text).asc())
                .limit(limit)
                .all()
            )
            return [(row.query_text, int(row.cnt)) for row in rows]
        finally:
            db.close()
    except Exception as e:
        logger.warning("统计高频检索问题失败",
                       extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})
        return []
