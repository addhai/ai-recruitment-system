"""检索埋点（retrieval_logs）的单测。

这里固定三件容易悄悄坏掉的事：

1. **模型定义了不等于库里有表**。ensure_tables() 走 alembic，
   漏写迁移时写埋点会静默失败（表不存在）——所以要真的跑一遍迁移，
   检查表和索引都在。
2. **写库失败不许影响主流程**。埋点是旁路可观测性，
   库挂了用户的问答也得照常返回。
3. **口径要能还原**。问句被截断后，hash 必须仍是入库文本的 sha256，
   否则"按 hash 聚类"和"按文本核对"会对不上。
"""
import hashlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect

from src.models import database
from src.services.retrieval_log import (
    QUERY_TEXT_MAX_LEN,
    purge_old_retrieval_logs,
    query_hash,
    top_queries,
    write_retrieval_log,
)


@pytest.fixture(autouse=True)
def _clean_table():
    """每个用例前清空检索日志表，避免用例之间互相污染。"""
    from src.models.database import RetrievalLog, SessionLocal

    db = SessionLocal()
    try:
        db.query(RetrievalLog).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield


def _rows():
    from src.models.database import RetrievalLog, SessionLocal

    db = SessionLocal()
    try:
        return db.query(RetrievalLog).order_by(RetrievalLog.id).all()
    finally:
        db.close()


def _insert(created_at, query_text="问题"):
    """直接塞一行指定时间的日志（保留期清理用例需要构造过期数据）。"""
    from src.models.database import RetrievalLog, SessionLocal

    db = SessionLocal()
    try:
        db.add(RetrievalLog(
            created_at=created_at,
            query_text=query_text,
            query_hash=query_hash(query_text),
        ))
        db.commit()
    finally:
        db.close()


class TestMigration:
    def test_migration_creates_table_and_indexes(self, tmp_path, monkeypatch):
        """迁移必须真的建出表和索引——模型定义存在不等于库里有表。"""
        url = f"sqlite:///{(tmp_path / 'retrieval.db').as_posix()}"
        eng = create_engine(url)
        monkeypatch.setattr(database, "engine", eng)
        monkeypatch.setattr(database.settings, "DATABASE_URL", url)

        database.migrate_database()

        insp = inspect(eng)
        assert "retrieval_logs" in insp.get_table_names()

        got = {c["name"] for c in insp.get_columns("retrieval_logs")}
        want = {c.name for c in database.Base.metadata.tables["retrieval_logs"].columns}
        assert want == got, "迁移建出的列与模型定义不一致"

        idx = {i["name"] for i in insp.get_indexes("retrieval_logs")}
        assert {"ix_retrieval_logs_created_at",
                "ix_retrieval_logs_query_hash"} <= idx, "缺少保留期清理/聚合用的索引"
        eng.dispose()

    def test_downgrade_drops_table(self, tmp_path, monkeypatch):
        """回滚要能把表删干净，否则回滚后再升级会撞"表已存在"。"""
        from alembic import command

        url = f"sqlite:///{(tmp_path / 'down.db').as_posix()}"
        eng = create_engine(url)
        monkeypatch.setattr(database, "engine", eng)
        monkeypatch.setattr(database.settings, "DATABASE_URL", url)

        cfg = database._alembic_config()
        command.upgrade(cfg, "head")
        assert "retrieval_logs" in inspect(eng).get_table_names()

        command.downgrade(cfg, "7f3a91c2d4e8")
        assert "retrieval_logs" not in inspect(eng).get_table_names()
        eng.dispose()


class TestWrite:
    def test_write_persists_row(self):
        write_retrieval_log(
            query_text="试用期工资怎么算？",
            top_k=4,
            bm25_titles=["员工入职流程"],
            vector_titles=["绩效考核制度"],
            merged_titles=["员工入职流程", "绩效考核制度"],
            bm25_ms=3.5,
            vector_ms=120.0,
            total_ms=130.25,
            mode="hybrid_rag",
            chunk_size=500,
            chunk_overlap=50,
            embedding_model="BAAI/bge-large-zh-v1.5",
            degraded=False,
        )

        rows = _rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.query_text == "试用期工资怎么算？"
        assert row.query_hash == hashlib.sha256("试用期工资怎么算？".encode("utf-8")).hexdigest()
        assert row.top_k == 4
        assert row.bm25_titles == ["员工入职流程"]
        assert row.vector_titles == ["绩效考核制度"]
        assert row.merged_titles == ["员工入职流程", "绩效考核制度"]
        assert row.bm25_ms == 3.5
        assert row.vector_ms == 120.0
        assert row.total_ms == 130.25
        assert row.mode == "hybrid_rag"
        assert row.chunk_size == 500
        assert row.chunk_overlap == 50
        assert row.embedding_model == "BAAI/bge-large-zh-v1.5"
        assert row.degraded is False
        assert row.created_at is not None

    def test_write_failure_does_not_break_caller(self, monkeypatch):
        """库挂了也不能让调用方失败——埋点是旁路。"""
        from src.models.database import RetrievalLog, SessionLocal

        def _boom():
            raise RuntimeError("database is down")

        monkeypatch.setattr(database, "SessionLocal", _boom)

        write_retrieval_log(query_text="这条不该影响主流程")  # 不抛异常即通过

        # 用未被替换的连接确认确实一行都没写进去
        db = SessionLocal()
        try:
            assert db.query(RetrievalLog).count() == 0
        finally:
            db.close()

    def test_unknown_kwargs_are_ignored(self):
        """调用方传错字段名不该把问答带崩。"""
        write_retrieval_log(query_text="正常问句", bm25_docs=[{"title": "入职流程"}])

        rows = _rows()
        assert len(rows) == 1
        assert rows[0].query_text == "正常问句"

    def test_query_text_is_truncated(self):
        """超长问句要截断，且 hash 必须是**入库后**文本的 sha256。"""
        long_q = "问" * (QUERY_TEXT_MAX_LEN + 500)
        write_retrieval_log(query_text=long_q)

        row = _rows()[0]
        assert len(row.query_text) == QUERY_TEXT_MAX_LEN
        assert row.query_hash == hashlib.sha256(
            ("问" * QUERY_TEXT_MAX_LEN).encode("utf-8")).hexdigest()

    def test_titles_accept_document_like_objects(self):
        """调用方可能传 langchain Document（标题在 metadata 里），正文不入库。"""

        class _Doc:
            def __init__(self, title, content):
                self.metadata = {"title": title}
                self.page_content = content

        write_retrieval_log(
            query_text="入职要带什么材料？",
            bm25_titles=[_Doc("员工入职流程", "身份证复印件……")],
        )

        row = _rows()[0]
        assert row.bm25_titles == ["员工入职流程"]
        assert "身份证" not in str(row.bm25_titles)

    def test_degraded_defaults_false(self):
        write_retrieval_log(query_text="默认不降级")
        assert _rows()[0].degraded is False


class TestRetention:
    def test_purge_deletes_expired_rows(self):
        now = datetime.utcnow()
        _insert(now - timedelta(days=100), "过期问题")
        _insert(now - timedelta(days=10), "还在的问题")

        deleted = purge_old_retrieval_logs(days=30)

        assert deleted == 1
        remaining = _rows()
        assert [r.query_text for r in remaining] == ["还在的问题"]

    def test_purge_keeps_recent_rows(self):
        _insert(datetime.utcnow(), "刚问的")

        assert purge_old_retrieval_logs(days=30) == 0
        assert len(_rows()) == 1

    def test_purge_with_zero_days_is_noop(self):
        _insert(datetime.utcnow() - timedelta(days=365), "很旧的问题")
        assert purge_old_retrieval_logs(days=0) == 0
        assert len(_rows()) == 1

    def test_purge_failure_returns_zero(self, monkeypatch):
        """清理失败属维护动作，不该把启动流程带崩。"""
        def _boom():
            raise RuntimeError("database is down")

        monkeypatch.setattr(database, "SessionLocal", _boom)
        assert purge_old_retrieval_logs(days=30) == 0

    def test_startup_cleans_expired_rows(self):
        """init_db() 启动时要清一遍——否则这张表只增不减，迟早撑爆磁盘。"""
        now = datetime.utcnow()
        _insert(now - timedelta(days=100), "过期问题")
        _insert(now, "还在的问题")

        database.init_db()

        assert [r.query_text for r in _rows()] == ["还在的问题"]


class TestTopQueries:
    def test_orders_by_count_desc(self):
        write_retrieval_log(query_text="年假有几天")
        write_retrieval_log(query_text="年假有几天")
        write_retrieval_log(query_text="年假有几天")
        write_retrieval_log(query_text="试用期工资")

        result = top_queries(limit=10)

        assert result[0] == ("年假有几天", 3)
        assert result[1] == ("试用期工资", 1)
        assert len(result) == 2

    def test_whitespace_variants_group_together(self):
        """多打几个空格不该算成两个问题（哈希前先 strip）。"""
        write_retrieval_log(query_text="年假有几天")
        write_retrieval_log(query_text="  年假有几天  ")

        result = top_queries(limit=10)
        assert result == [("年假有几天", 2)]

    def test_limit_is_respected(self):
        for i in range(5):
            write_retrieval_log(query_text=f"问题{i}")

        assert len(top_queries(limit=2)) == 2
        assert top_queries(limit=0) == []

    def test_failure_returns_empty_list(self, monkeypatch):
        def _boom():
            raise RuntimeError("database is down")

        monkeypatch.setattr(database, "SessionLocal", _boom)
        assert top_queries(limit=10) == []
