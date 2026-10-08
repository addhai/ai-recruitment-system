# -*- coding: utf-8 -*-
"""知识库自定义文档持久化测试。

回归背景：自定义文档此前只存在内存列表 _extra_documents 中，重启即丢——
文档列表不显示、BM25 检索丢失；若期间触发向量库维度重建，还会从向量库
里一并消失（重建用的 split_docs 已不含这些文档）。

另覆盖向量库重建不应静默删数据（原先直接 rmtree）。
"""
import inspect

import pytest

from src.models.database import SessionLocal, KnowledgeDocument


@pytest.fixture(autouse=True)
def _clean_docs():
    """用例前后清理自定义文档，避免测试间互相影响"""
    def _purge():
        with SessionLocal() as db:
            db.query(KnowledgeDocument).filter(
                KnowledgeDocument.title.like("单测%")).delete(synchronize_session=False)
            db.commit()
    _purge()
    yield
    _purge()


class TestCustomDocumentPersistence:
    def test_add_persists_to_database(self):
        from src.rag import knowledge_base as kb
        with SessionLocal() as db:
            before = db.query(KnowledgeDocument).filter(
                KnowledgeDocument.title.like("单测%")).count()

        kb.add_document("单测-落库验证", "这是一条用于单测的自定义文档内容。")

        with SessionLocal() as db:
            rows = db.query(KnowledgeDocument).filter(
                KnowledgeDocument.title.like("单测%")).all()
        assert len(rows) == before + 1
        assert rows[-1].content.startswith("这是一条用于单测")

    def test_survives_simulated_restart(self):
        """核心回归：清空进程内缓存后重新初始化，文档必须仍存在"""
        from src.rag import knowledge_base as kb

        kb.add_document("单测-重启存活", "重启后仍应存在的内容。")

        # 模拟进程重启：清空所有进程内状态
        kb._extra_documents = []
        kb._init_attempted = False
        kb._vector_store = None
        kb._bm25_retriever = None

        kb.init_knowledge_base()

        titles = [d["title"] for d in kb._extra_documents]
        assert any(t == "单测-重启存活" for t in titles), \
            f"重启后自定义文档丢失，当前缓存: {titles}"

    def test_delete_removes_from_database_and_cache(self):
        from src.rag import knowledge_base as kb

        doc_id = kb.add_document("单测-待删除", "这条会被删除。")
        assert any(d["id"] == doc_id for d in kb._extra_documents)

        assert kb.delete_document(doc_id) is True

        with SessionLocal() as db:
            assert db.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id).count() == 0
        assert not any(d.get("id") == doc_id for d in kb._extra_documents)

    def test_delete_missing_returns_false(self):
        from src.rag import knowledge_base as kb
        assert kb.delete_document(99999999) is False

    def test_reload_keeps_cache_on_db_failure(self, monkeypatch):
        """加载失败时保留现有缓存并告警，不让知识库整体不可用"""
        from src.rag import knowledge_base as kb
        kb.add_document("单测-容错", "内容")
        before = len(kb._extra_documents)

        import src.models.database as dbmod

        def _boom():
            raise RuntimeError("数据库不可用")

        monkeypatch.setattr(dbmod, "ensure_tables", _boom)
        kb._reload_custom_documents()  # 不应抛异常

        assert len(kb._extra_documents) == before, "加载失败不应清空缓存"


class TestVectorDirResetSafety:
    """向量库重建不应静默销毁数据"""

    def test_reset_backs_up_instead_of_deleting(self):
        from src.rag import knowledge_base as kb
        src = inspect.getsource(kb._reset_vector_dir)
        assert "os.rename" in src, "应先重命名为备份目录"
        assert "logger.warning" in src, "必须留下明确告警"
        # rmtree 只应出现在备份失败的回退分支，不能是主路径
        assert src.index("os.rename") < src.index("shutil.rmtree"), \
            "主路径必须是备份，rmtree 仅作回退"

    def test_reset_is_noop_when_dir_absent(self, tmp_path, monkeypatch):
        from src.rag import knowledge_base as kb
        target = tmp_path / "no_such_dir"
        monkeypatch.setattr(kb, "PERSIST_DIR", str(target))
        kb._reset_vector_dir()  # 不应抛异常
        assert target.exists(), "无论目录是否存在都应创建出来"