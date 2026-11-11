# -*- coding: utf-8 -*-
"""RAG 融合与索引口径的守门测试。

## 为什么这组测试必须存在

原实现里"混合检索"是假的：先把 BM25 结果全部塞进列表，再把向量结果追加
到后面，最后 `merged[:k]` 截断。但 `BM25Retriever` 的 k 就是 4，且它对
**任何**查询都回满 4 条（连「今天股市行情如何」这种完全无关的问题也回 4 条，
实测确认）——列表在轮到向量结果之前就已经满了，向量结果被整段截掉。

后果是：向量库照建（每次重建还要重嵌全库、花 embedding 成本），线上却一条
都不读；`mode` 还照报 `hybrid_rag`，标签会骗人。

所以这里用**桩检索器**把"两路都必须真正进入上下文"钉成契约——
它不依赖真实 embedding，因此离线、秒级、可进日常 CI。
"""
import hashlib

import pytest

from src.rag import knowledge_base as kb


# ================================================================ 桩件

class _Doc:
    """最小 Document 替身：检索链只用到 metadata 与 page_content。"""

    def __init__(self, title, content=""):
        self.metadata = {"title": title, "source": "system"}
        self.page_content = content or title


class _StubBM25:
    """固定返回若干条的 BM25 桩。"""

    def __init__(self, titles):
        self.titles = titles

    def invoke(self, query):
        return [_Doc(t) for t in self.titles]


class _StubVector:
    """固定返回若干条的向量检索桩。"""

    def __init__(self, titles):
        self.titles = titles

    def similarity_search(self, query, k=4):
        return [_Doc(t) for t in self.titles[:k]]


class _FakeEmbeddings:
    """确定性假 embedding：同一段文本永远得到同一向量，不联网。

    用哈希而非随机数，是为了让"换模型"这类测试可复现——
    随机向量会让相似度排序变成噪声，测不出任何东西。
    """

    def __init__(self, salt=""):
        self.salt = salt

    def _v(self, text):
        h = hashlib.sha256((self.salt + text).encode("utf-8")).digest()
        return [b / 255 for b in h[:16]]

    def embed_documents(self, texts):
        return [self._v(t) for t in texts]

    def embed_query(self, text):
        return self._v(text)


@pytest.fixture
def _isolated_kb(tmp_path, monkeypatch):
    """把知识库状态与持久目录隔离到临时位置，用完还原。

    必须还原 system_documents：它是模块级列表，测试往里追加文档会污染
    后续所有测试的检索结果。
    """
    monkeypatch.setattr(kb, "PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setattr(kb, "_build_chat_llm", lambda: None)
    # 重排必须显式关掉：它是一次外部 API 调用，会改变候选顺序。
    # 这组用例断言的是融合顺序，不能被重排插一脚。
    monkeypatch.setattr(kb.settings, "RERANK_ENABLED", False)
    original_docs = list(kb.system_documents)
    original_store = kb._vector_store
    original_bm25 = kb._bm25_retriever
    kb._init_attempted = False
    try:
        yield
    finally:
        kb.system_documents[:] = original_docs
        kb._vector_store = original_store
        kb._bm25_retriever = original_bm25
        kb._init_attempted = False


# ================================================================ 融合契约

class TestFusionContract:
    """两路召回都必须真正进入上下文——这是"混合检索"名副其实的前提。"""

    def test_vector_results_reach_the_context(self, monkeypatch, _isolated_kb):
        monkeypatch.setattr(kb, "_bm25_retriever",
                            _StubBM25(["BM25-A", "BM25-B", "BM25-C", "BM25-D"]))
        monkeypatch.setattr(kb, "_vector_store",
                            _StubVector(["VEC-A", "VEC-B", "VEC-C", "VEC-D"]))

        docs, stats = kb._retrieve_with_stats("任意问题", k=4)
        titles = [d.metadata["title"] for d in docs]

        assert any(t.startswith("VEC-") for t in titles), \
            f"向量结果一条都没进入上下文（实际 {titles}）——融合退回 BM25-only 了"
        assert any(t.startswith("BM25-") for t in titles), \
            f"BM25 结果一条都没进入上下文（实际 {titles}）"

    def test_vector_results_survive_when_bm25_is_full(self, monkeypatch, _isolated_kb):
        """BM25 塞满 k 条时向量仍要能进——这正是原 bug 的触发条件。"""
        monkeypatch.setattr(kb, "_bm25_retriever",
                            _StubBM25(["BM25-A", "BM25-B", "BM25-C", "BM25-D"]))
        monkeypatch.setattr(kb, "_vector_store", _StubVector(["VEC-A"]))

        docs, _ = kb._retrieve_with_stats("q", k=4)
        titles = [d.metadata["title"] for d in docs]
        assert "VEC-A" in titles, \
            f"BM25 塞满时向量被截掉了（实际 {titles}）——这是原 bug 的精确复现条件"

    def test_bm25_only_still_works(self, monkeypatch, _isolated_kb):
        """没有向量库时，退化为纯 BM25，顺序保持不变。"""
        monkeypatch.setattr(kb, "_bm25_retriever",
                            _StubBM25(["BM25-A", "BM25-B", "BM25-C"]))
        monkeypatch.setattr(kb, "_vector_store", None)

        docs, stats = kb._retrieve_with_stats("q", k=4)
        assert [d.metadata["title"] for d in docs] == ["BM25-A", "BM25-B", "BM25-C"]
        assert stats["mode"] == "bm25_llm"

    def test_vector_only_works(self, monkeypatch, _isolated_kb):
        monkeypatch.setattr(kb, "_bm25_retriever", None)
        monkeypatch.setattr(kb, "_vector_store", _StubVector(["VEC-A", "VEC-B"]))

        docs, stats = kb._retrieve_with_stats("q", k=4)
        assert [d.metadata["title"] for d in docs] == ["VEC-A", "VEC-B"]
        assert stats["mode"] == "vector_llm"

    def test_both_sources_means_hybrid_mode(self, monkeypatch, _isolated_kb):
        monkeypatch.setattr(kb, "_bm25_retriever", _StubBM25(["BM25-A"]))
        monkeypatch.setattr(kb, "_vector_store", _StubVector(["VEC-A"]))
        _docs, stats = kb._retrieve_with_stats("q", k=4)
        assert stats["mode"] == "hybrid_rag"

    def test_no_retriever_reports_fallback(self, monkeypatch, _isolated_kb):
        monkeypatch.setattr(kb, "_bm25_retriever", None)
        monkeypatch.setattr(kb, "_vector_store", None)
        _docs, stats = kb._retrieve_with_stats("q", k=4)
        assert stats["mode"] == "fallback"
        assert stats["degraded"] is False


class TestRRFOrdering:
    """RRF 融合的排序性质。"""

    def test_document_in_both_sources_ranks_higher(self, monkeypatch, _isolated_kb):
        """两路都认的文档应排在只有一路认的前面。"""
        monkeypatch.setattr(kb, "_bm25_retriever", _StubBM25(["SHARED", "BM25-ONLY"]))
        monkeypatch.setattr(kb, "_vector_store", _StubVector(["SHARED", "VEC-ONLY"]))

        docs, _ = kb._retrieve_with_stats("q", k=4)
        titles = [d.metadata["title"] for d in docs]
        assert titles[0] == "SHARED", \
            f"两路都命中的文档应排第一（实际 {titles}）"

    def test_duplicate_documents_are_merged_not_duplicated(self, monkeypatch, _isolated_kb):
        monkeypatch.setattr(kb, "_bm25_retriever", _StubBM25(["DUP", "A"]))
        monkeypatch.setattr(kb, "_vector_store", _StubVector(["DUP", "B"]))

        docs, _ = kb._retrieve_with_stats("q", k=4)
        titles = [d.metadata["title"] for d in docs]
        assert len(titles) == len(set(titles)), f"同一文档被重复放入上下文：{titles}"

    def test_result_count_never_exceeds_k(self, monkeypatch, _isolated_kb):
        monkeypatch.setattr(kb, "_bm25_retriever",
                            _StubBM25([f"BM{i}" for i in range(4)]))
        monkeypatch.setattr(kb, "_vector_store", _StubVector([f"VEC{i}" for i in range(4)]))
        docs, _ = kb._retrieve_with_stats("q", k=3)
        assert len(docs) <= 3

    def test_fusion_is_deterministic(self, monkeypatch, _isolated_kb):
        """同样的输入必须给出同样的顺序，否则评测结果不可复现。"""
        monkeypatch.setattr(kb, "_bm25_retriever", _StubBM25(["A", "B", "C"]))
        monkeypatch.setattr(kb, "_vector_store", _StubVector(["D", "A"]))
        first = [d.metadata["title"] for d in kb._retrieve_with_stats("q", k=4)[0]]
        second = [d.metadata["title"] for d in kb._retrieve_with_stats("q", k=4)[0]]
        assert first == second


class TestDegradation:
    """一路挂了不能让整条检索链路失效。"""

    def test_vector_failure_keeps_bm25_results(self, monkeypatch, _isolated_kb):
        monkeypatch.setattr(kb, "_bm25_retriever", _StubBM25(["BM25-A", "BM25-B"]))

        class _BoomVector:
            def similarity_search(self, query, k=4):
                raise RuntimeError("向量库不可用")

        monkeypatch.setattr(kb, "_vector_store", _BoomVector())

        docs, stats = kb._retrieve_with_stats("q", k=4)
        assert [d.metadata["title"] for d in docs] == ["BM25-A", "BM25-B"]
        assert stats["degraded"] is True
        assert stats["mode"] == "bm25_llm"

    def test_bm25_failure_keeps_vector_results(self, monkeypatch, _isolated_kb):
        class _BoomBM25:
            def invoke(self, query):
                raise RuntimeError("BM25 不可用")

        monkeypatch.setattr(kb, "_bm25_retriever", _BoomBM25())
        monkeypatch.setattr(kb, "_vector_store", _StubVector(["VEC-A"]))

        docs, stats = kb._retrieve_with_stats("q", k=4)
        assert [d.metadata["title"] for d in docs] == ["VEC-A"]
        assert stats["degraded"] is True
        assert stats["mode"] == "vector_llm"


# ================================================================ 索引口径指纹

class TestIndexFingerprint:
    """口径指纹：换 embedding 模型 / 改切分参数，必须触发重建。"""

    def _init(self, monkeypatch, salt="", model="emb-A"):
        monkeypatch.setattr(kb, "_build_embeddings", lambda: _FakeEmbeddings(salt))
        monkeypatch.setattr(kb.settings, "EMBEDDING_MODEL", model)
        kb._init_attempted = False
        kb.init_knowledge_base(force=True)
        return kb._vector_store

    def test_fingerprint_is_stamped_into_collection_metadata(self, monkeypatch, _isolated_kb):
        store = self._init(monkeypatch)
        meta = dict(store._collection.metadata or {})
        assert meta.get("kb_embedding_model") == "emb-A"
        assert meta.get("kb_chunk_size") == kb.chunk_config()["chunk_size"]
        assert meta.get("kb_chunk_overlap") == kb.chunk_config()["chunk_overlap"]
        assert "kb_embedding_dim" in meta

    def test_same_fingerprint_does_not_rebuild(self, monkeypatch, _isolated_kb):
        """口径一致时不该重建——重建要重新嵌入全部文档，代价不低。"""
        store = self._init(monkeypatch)
        before = store._collection.count()

        monkeypatch.setattr(kb, "_build_embeddings", lambda: _FakeEmbeddings())
        monkeypatch.setattr(kb.settings, "EMBEDDING_MODEL", "emb-A")
        kb._init_attempted = False
        kb.init_knowledge_base(force=False)

        assert kb._vector_store._collection.count() == before

    def test_changed_embedding_model_triggers_rebuild(self, monkeypatch, _isolated_kb):
        """换 embedding 模型（**同维度**）必须重建。

        这是原来漏掉的那一类退化：维度探测只认维度，换一个同样是 1024 维的
        模型，探测照样通过，旧向量被静默复用 → 拿新模型的 query 去检索旧
        模型的文档向量，检索结果悄悄变差且无从分辨。
        """
        self._init(monkeypatch, model="emb-A")

        monkeypatch.setattr(kb, "_build_embeddings", lambda: _FakeEmbeddings("new"))
        monkeypatch.setattr(kb.settings, "EMBEDDING_MODEL", "emb-B")
        kb._init_attempted = False
        kb.init_knowledge_base(force=False)

        meta = dict(kb._vector_store._collection.metadata or {})
        assert meta.get("kb_embedding_model") == "emb-B", \
            "换模型后指纹没有更新，说明旧向量被继续使用了"

    def test_changed_chunk_params_trigger_rebuild(self, monkeypatch, _isolated_kb):
        """改切分参数必须重建：块边界变了，库里的向量对应的就是旧切分结果。"""
        self._init(monkeypatch)

        monkeypatch.setattr(kb, "_build_embeddings", lambda: _FakeEmbeddings())
        monkeypatch.setattr(kb.settings, "KB_CHUNK_SIZE", 300)
        kb._init_attempted = False
        kb.init_knowledge_base(force=False)

        meta = dict(kb._vector_store._collection.metadata or {})
        assert meta.get("kb_chunk_size") == 300, \
            "改了切分参数但指纹没更新，旧切分的向量还在库里"

    def test_legacy_index_without_fingerprint_is_rebuilt(self, monkeypatch, _isolated_kb):
        """没有指纹的老索引（来源不明）必须重建一次。

        来源不明的索引属于"可能不可比"：按项目一贯原则，宁可多重建一次，
        也不把口径不明的向量当成可用的。

        老索引的模拟方式：collection metadata 里有别的键、但没有 kb_ 前缀的
        指纹项——这正是指纹机制引入之前建出来的索引的样子。
        （不能用空 metadata 模拟：Chroma 拒绝空 metadata，会直接抛错。）
        """
        store = self._init(monkeypatch)
        store._collection.modify(metadata={"legacy_marker": "1"})
        assert "kb_embedding_model" not in (store._collection.metadata or {})

        monkeypatch.setattr(kb, "_build_embeddings", lambda: _FakeEmbeddings())
        monkeypatch.setattr(kb.settings, "EMBEDDING_MODEL", "emb-A")
        kb._init_attempted = False
        kb.init_knowledge_base(force=False)

        meta = dict(kb._vector_store._collection.metadata or {})
        assert meta.get("kb_embedding_model") == "emb-A", \
            "无指纹的索引没有重建，口径仍然不明"


class TestIndexConsistency:
    """索引与当前语料必须一致——否则 BM25 与向量会各说各话。"""

    def test_new_system_document_reaches_vector_index(self, monkeypatch, _isolated_kb):
        """新增系统文档后，BM25 与向量库必须同时包含它。

        原实现里非 force 路径只在 count==0 时才 add_documents，于是新文档
        只进了 BM25、没进向量库 → 两路索引内容分叉。
        """
        monkeypatch.setattr(kb, "_build_embeddings", lambda: _FakeEmbeddings())
        kb._init_attempted = False
        kb.init_knowledge_base(force=True)
        before = kb._vector_store._collection.count()

        kb.system_documents.append({
            "title": "新增制度-一致性验证",
            "content": "新增制度-一致性验证：差旅报销上限为每次800元。",
        })
        kb._init_attempted = False
        kb.init_knowledge_base(force=False)

        assert kb._vector_store._collection.count() == before + 1, \
            "新增系统文档没有进入向量库，BM25 与向量索引已分叉"

        metas = kb._vector_store._collection.get(include=["metadatas"])["metadatas"]
        titles = {m.get("title") for m in metas}
        assert "新增制度-一致性验证" in titles

    def test_removed_system_document_triggers_rebuild(self, monkeypatch, _isolated_kb):
        monkeypatch.setattr(kb, "_build_embeddings", lambda: _FakeEmbeddings())
        kb._init_attempted = False
        kb.init_knowledge_base(force=True)
        before = kb._vector_store._collection.count()

        kb.system_documents.pop()
        kb._init_attempted = False
        kb.init_knowledge_base(force=False)

        assert kb._vector_store._collection.count() == before - 1
