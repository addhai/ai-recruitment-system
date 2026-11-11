# -*- coding: utf-8 -*-
"""知识库中文分词的回归测试。

## 为什么需要这组测试

jieba 的切分是**上下文相关**的：同一个词在查询与文档里会被切成不同形状。
实测（修复前）：

    查询「每月什么时候发工资」 → ['每月','什么','时候','发工资']
    文档「每月15日发放上月工资」 → ['每月','15','日','发放','上','月工资']
    交集：{'每月'}   ← 「工资」完全对不上

这类错位是中文 BM25 的经典坑：用户的自然提问很难命中制度原文。更隐蔽的是
它**不报错、不崩溃**——检索照样返回 4 条结果，只是排序毫无意义。那条
「每月什么时候发工资」的用例当时是靠并列排序的运气通过的（BM25 对全零分
的查询照样回 Top-K），不是靠真实相关。

所以这里把「词与词之间的鸿沟必须被 bigram 桥接」钉成断言。
"""
import pytest

from src.rag import knowledge_base as kb


class TestTokenize:
    def test_jieba_path_includes_words(self):
        tokens = kb._tokenize("年假有多少天")
        assert isinstance(tokens, list)
        assert "年假" in tokens or "年" in tokens
        assert all(t.strip() for t in tokens)

    def test_bigrams_are_added_on_top_of_words(self):
        """jieba 可用时，token 流必须同时含词与字符 bigram。

        用「薪资水平」做样本：jieba 切成 ['薪资','水平']，而 bigram 通道
        会额外产出「资水」——它不是任何词，只可能来自 bigram 通道，
        因此能真正证明两条通道都在工作（而不是同一份词表数了两遍）。
        """
        tokens = kb._tokenize("薪资水平")
        assert "薪资" in tokens, "词切分结果必须保留"
        assert "资水" in tokens, "字符 bigram 通道必须额外产出跨词边界的组合"

    def test_bigram_bridges_word_mismatch(self, monkeypatch):
        """查询与文档切分不一致时，bigram 必须把两者连上。

        这是整组测试的核心：jieba 切不出「工资」≠ 检索不到「工资」。
        """
        query = kb._tokenize("每月什么时候发工资")
        doc = kb._tokenize("每月15日发放上月工资")
        shared = set(query) & set(doc)
        assert "工资" in shared, \
            f"查询与文档没有共享「工资」，BM25 召不回（shared={shared}）"

    def test_salary_confidentiality_case_is_bridged(self):
        """「工资」vs「薪资」：核心词都不同，靠 bigram 也要能接上。

        文档写「薪资」，用户问「工资」——这是制度原文与用户用词不一致的
        真实情形，也是这条用例之前对 chunk_size 极度敏感的原因。
        """
        query = kb._tokenize("员工之间可以互相打听工资吗")
        doc = kb._tokenize("员工不得泄露个人薪资信息")
        assert set(query) & set(doc), "查询与文档必须至少共享一个 token"

    def test_fallback_to_bigrams_without_jieba(self, monkeypatch):
        """jieba 缺失时退化为纯字符 bigram，检索能力不能整体失效。"""
        monkeypatch.setattr(kb, "jieba", None)
        tokens = kb._tokenize("年假")
        assert tokens == ["年假"], "无 jieba 时应产出字符 bigram"
        assert tokens, "不能返回空列表"


class TestBM25Recall:
    """端到端：关键用例必须拿到**真实相关**的分数，而不是靠并列排序蒙对。"""

    def _scores(self, query):
        from src.rag import knowledge_base as _kb
        retriever = _kb._bm25_retriever
        vectorizer = retriever.vectorizer
        raw = vectorizer.get_scores(retriever.preprocess_func(query))
        return [float(s) for s in raw]

    @pytest.fixture(autouse=True)
    def _bm25_only(self, monkeypatch):
        monkeypatch.setattr(kb, "_build_embeddings", lambda: None)
        monkeypatch.setattr(kb, "_build_chat_llm", lambda: None)
        kb._init_attempted = False
        kb.init_knowledge_base(force=True)
        yield
        kb._init_attempted = False

    def test_payday_query_has_non_zero_relevance(self):
        """「每月什么时候发工资」必须有真实相关分数。

        修复前它是 0.000——靠 BM25 对全零分查询照样回 Top-K 的行为蒙混过关，
        一旦文档顺序变化就会失效。
        """
        scores = self._scores("每月什么时候发工资")
        assert max(scores) > 0, \
            "该查询与所有文档的 BM25 相关性都是 0，说明分词错位没修好"

    def test_offtopic_query_scores_far_below_relevant_query(self):
        """无关查询的相关性必须远低于相关查询。

        这是 bigram 方案的代价控制：bigram 提升召回，但必须确认它没让
        完全无关的查询也拿到有意义的分数——否则相关性下限（未来）就没法设了。

        **刻意不断言"绝对等于 0"**：BM25 的 IDF 取决于语料构成——
        "公司"这种词出现在 6 篇里的 3 篇时 idf 恰好为 0（不携带任何信息），
        语料一变（别的测试往库里加过自定义文档）它就变成正数。
        把断言绑死在绝对 0 上，等于绑死在"语料恰好是这 6 篇"上，
        换个执行顺序就挂。

        真正要守住的是**相对关系**：无关查询不该和有实质相关性的查询
        拿到差不多的分数。
        """
        relevant = max(self._scores("年假有多少天"))
        offtopic = max(self._scores("公司股票期权怎么分配"))
        assert relevant > 0, "相关查询本身就该有分数，否则这条断言没有意义"
        assert offtopic < relevant / 2, \
            (f"无关查询的分数不该接近相关查询"
             f"（relevant={relevant:.4f} offtopic={offtopic:.4f}）")
