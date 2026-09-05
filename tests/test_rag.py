# -*- coding: utf-8 -*-
"""知识库 RAG 单测：聚焦不依赖网络的部分。

embedding/LLM 都是外部服务，测试中 monkeypatch 掉：
- _build_embeddings 返回 None，强制走 BM25 路径（不发任何网络请求）
- 不调用 _qa_chain.invoke（LLM 网络调用），改测检索与降级结构
"""
import pytest

from src.rag import knowledge_base as kb


@pytest.fixture(autouse=True)
def _bm25_only(monkeypatch):
    """所有用例都在「无 embedding」模式下运行，保证离线可重复"""
    monkeypatch.setattr(kb, "_build_embeddings", lambda: None)
    # 清空自定义文档，保证用例间隔离
    kb._extra_documents.clear()
    kb.init_knowledge_base(force=True)
    yield
    kb._extra_documents.clear()


class TestTokenize:
    def test_jieba_tokenizes_chinese(self):
        tokens = kb._tokenize("年假有多少天")
        assert isinstance(tokens, list)
        assert "年假" in tokens or "年" in tokens
        assert all(t.strip() for t in tokens)

    def test_fallback_bigram_without_jieba(self, monkeypatch):
        monkeypatch.setattr(kb, "jieba", None)
        tokens = kb._tokenize("年假")
        # 退化 bi-gram：["年假"]
        assert tokens == ["年假"]


class TestBM25Retrieval:
    def test_init_builds_bm25_without_vector(self):
        assert kb._bm25_retriever is not None
        assert kb._vector_store is None

    def test_keyword_query_hits_welfare_doc(self):
        docs = kb._retrieve_docs("年假有多少天")
        assert docs, "BM25 应返回结果"
        titles = [d.metadata.get("title") for d in docs]
        assert "员工福利制度" in titles

    def test_bm25_uses_jieba_for_subword_match(self):
        # "休假" 与文档中的 "带薪休假/年假" 经 jieba 分词后可匹配
        docs = kb._retrieve_docs("休假制度")
        titles = [d.metadata.get("title") for d in docs]
        assert "员工福利制度" in titles

    def test_custom_document_is_searchable(self):
        kb.add_document("远程办公制度（单测）", "员工每周可申请2天远程办公，需提前在OA审批。")
        docs = kb._retrieve_docs("每周可以在家办公几天")
        titles = [d.metadata.get("title") for d in docs]
        assert "远程办公制度（单测）" in titles

    def test_custom_document_survives_force_rebuild(self):
        kb.add_document("弹性打卡制度（单测）", "核心工作时间10点到16点，其余时间弹性。")
        kb.init_knowledge_base(force=True)  # 重建不应丢自定义文档
        docs = kb._retrieve_docs("核心工作时间是几点")
        titles = [d.metadata.get("title") for d in docs]
        assert "弹性打卡制度（单测）" in titles


class TestFallback:
    def test_out_of_scope_question_returns_guidance(self):
        result = kb.fallback_answer("公司股票期权怎么分配？")
        # 无关键词命中时给出引导话术，sources 为空
        assert result["mode"] == "fallback"
        assert result["sources"] == []

    def test_known_question_returns_template(self):
        result = kb.fallback_answer("年假多少天")
        assert result["mode"] == "fallback"
        assert result["sources"]
        assert any("福利" in s["title"] for s in result["sources"])

    def test_query_sync_degrades_to_fallback_without_llm(self, monkeypatch):
        # 模拟 LLM 链未建立（如无 LLM_API_KEY）
        monkeypatch.setattr(kb, "_qa_chain", None)
        result = kb._query_sync("年假有多少天")
        assert result["mode"] == "fallback"
        assert "5天" in result["answer"]

    def test_query_sync_handles_llm_failure(self, monkeypatch):
        # 模拟 LLM 调用抛异常，应降级模板而不是 500
        class _BoomChain:
            def invoke(self, _):
                raise RuntimeError("LLM 服务不可用")

        monkeypatch.setattr(kb, "_qa_chain", _BoomChain())
        result = kb._query_sync("年假有多少天")
        assert result["mode"] == "fallback"
        assert result["answer"]


class TestAsyncEntry:
    def test_async_entry_returns_same_shape(self, monkeypatch):
        import asyncio
        monkeypatch.setattr(kb, "_qa_chain", None)
        result = asyncio.run(kb.query_knowledge_base_async("试用期多久"))
        assert "answer" in result and "sources" in result and "mode" in result
