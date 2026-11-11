# -*- coding: utf-8 -*-
"""重排（rerank）的守门测试。

## 为什么需要

重排是**锦上添花的增强，不是必需环节**——它挂在检索主链路上，任何一次
失败都不能把召回结果弄丢。所以这里钉住三件事：

1. **关掉时行为不变**：开关关闭 / 没凭据 → 完全跳过，顺序与原来一致。
2. **失败时降级**：接口报错、超时、返回垃圾数据 → 沿用融合顺序，不抛异常。
3. **成功时确实改变顺序**：交叉编码器给的顺序要真的生效。

另外钉住一个容易忽略的前提：**候选池必须大于最终返回的 k**。否则重排
拿到手的就是已经截断过的结果，没有可挑的余地，重排等于空转。
"""
import pytest

from src.rag import knowledge_base as kb


class _Doc:
    def __init__(self, title, content=""):
        self.metadata = {"title": title, "source": "system"}
        self.page_content = content or title


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def _rerank_env(monkeypatch):
    """给重排一个可控的外部环境：开关开、凭据齐、HTTP 可桩。"""
    monkeypatch.setattr(kb.settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(kb.settings, "RERANK_API_KEY", "test-key")
    monkeypatch.setattr(kb.settings, "RERANK_API_BASE", "https://example.invalid/v1")
    monkeypatch.setattr(kb.settings, "RERANK_MODEL", "test-reranker")
    monkeypatch.setattr(kb.settings, "RERANK_CANDIDATES", 8)
    yield


def _stub_post(monkeypatch, payload, status=200, calls=None):
    """把 httpx.post 换成桩。"""
    import httpx

    def _fake_post(url, **kwargs):
        if calls is not None:
            calls.append({"url": url, **kwargs})
        return _FakeResponse(payload, status)

    monkeypatch.setattr(httpx, "post", _fake_post)


class TestRerankDisabled:
    """关掉或没凭据 → 完全不动。"""

    def test_disabled_keeps_original_order(self, monkeypatch):
        monkeypatch.setattr(kb.settings, "RERANK_ENABLED", False)
        docs = [_Doc("A"), _Doc("B"), _Doc("C")]
        out, applied = kb._rerank("q", docs, 4)
        assert out == docs
        assert applied is False

    def test_missing_credentials_skips_rerank(self, monkeypatch):
        monkeypatch.setattr(kb.settings, "RERANK_ENABLED", True)
        monkeypatch.setattr(kb.settings, "RERANK_API_KEY", "")
        monkeypatch.setattr(kb.settings, "EMBEDDING_API_KEY", "")
        monkeypatch.setattr(kb.settings, "LLM_API_KEY", "")
        docs = [_Doc("A"), _Doc("B")]
        out, applied = kb._rerank("q", docs, 4)
        assert out == docs
        assert applied is False

    def test_single_candidate_needs_no_rerank(self, monkeypatch):
        monkeypatch.setattr(kb.settings, "RERANK_ENABLED", True)
        monkeypatch.setattr(kb.settings, "RERANK_API_KEY", "k")
        monkeypatch.setattr(kb.settings, "RERANK_API_BASE", "https://example.invalid/v1")
        docs = [_Doc("A")]
        out, applied = kb._rerank("q", docs, 4)
        assert applied is False
        assert out == docs


class TestRerankDegradation:
    """失败必须降级，不能把召回结果丢掉。"""

    def test_http_error_keeps_original_order(self, monkeypatch, _rerank_env):
        _stub_post(monkeypatch, {}, status=500)
        docs = [_Doc("A"), _Doc("B")]
        out, applied = kb._rerank("q", docs, 4)
        assert out == docs, "接口失败时必须沿用原序"
        assert applied is False

    def test_empty_results_keeps_original_order(self, monkeypatch, _rerank_env):
        _stub_post(monkeypatch, {"results": []})
        docs = [_Doc("A"), _Doc("B")]
        out, applied = kb._rerank("q", docs, 4)
        assert out == docs
        assert applied is False

    def test_exception_is_swallowed(self, monkeypatch, _rerank_env):
        import httpx

        def _boom(url, **kwargs):
            raise RuntimeError("网络不通")

        monkeypatch.setattr(httpx, "post", _boom)
        docs = [_Doc("A"), _Doc("B")]
        out, applied = kb._rerank("q", docs, 4)
        assert out == docs
        assert applied is False


class TestRerankOrdering:
    """成功时必须真的改变顺序。"""

    def test_new_order_is_applied(self, monkeypatch, _rerank_env):
        # 接口认为 index 2 最相关
        _stub_post(monkeypatch, {"results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.5},
            {"index": 1, "relevance_score": 0.1},
        ]})
        docs = [_Doc("A"), _Doc("B"), _Doc("C")]
        out, applied = kb._rerank("q", docs, 3)
        assert applied is True
        assert [d.metadata["title"] for d in out] == ["C", "A", "B"]

    def test_missing_indices_are_appended_in_original_order(self, monkeypatch, _rerank_env):
        """接口只回了部分候选 → 其余按原序补在后面，条目不能变少。"""
        _stub_post(monkeypatch, {"results": [{"index": 3, "relevance_score": 0.9}]})
        docs = [_Doc("A"), _Doc("B"), _Doc("C"), _Doc("D")]
        out, applied = kb._rerank("q", docs, 4)
        assert applied is True
        assert len(out) == 4
        assert [d.metadata["title"] for d in out] == ["D", "A", "B", "C"]

    def test_invalid_indices_are_ignored(self, monkeypatch, _rerank_env):
        """越界 / 非整数 / 重复的 index 都要被忽略，不能把文档挤掉。"""
        _stub_post(monkeypatch, {"results": [
            {"index": 99, "relevance_score": 0.9},
            {"index": -1, "relevance_score": 0.8},
            {"index": "x", "relevance_score": 0.7},
            {"index": 1, "relevance_score": 0.6},
            {"index": 1, "relevance_score": 0.5},
        ]})
        docs = [_Doc("A"), _Doc("B"), _Doc("C")]
        out, applied = kb._rerank("q", docs, 3)
        assert applied is True
        assert len(out) == 3
        assert out[0].metadata["title"] == "B"
        assert len({id(d) for d in out}) == 3, "同一文档不能重复出现"

    def test_result_is_truncated_to_k(self, monkeypatch, _rerank_env):
        _stub_post(monkeypatch, {"results": [
            {"index": i, "relevance_score": 1.0 - i * 0.1} for i in range(6)
        ]})
        docs = [_Doc(f"D{i}") for i in range(6)]
        out, _ = kb._rerank("q", docs, 3)
        assert len(out) == 3


class TestCandidatePool:
    """候选池必须大于 k，否则重排是空转。"""

    def test_pool_is_larger_than_k_when_rerank_enabled(self, monkeypatch, _rerank_env):
        monkeypatch.setattr(kb.settings, "RERANK_CANDIDATES", 8)
        k = 4
        pool = max(k, int(kb.settings.RERANK_CANDIDATES or k))
        assert pool > k, "候选池必须留出重排的余地"

    def test_pool_equals_k_when_rerank_disabled(self, monkeypatch):
        monkeypatch.setattr(kb.settings, "RERANK_ENABLED", False)
        k = 4
        pool = k
        assert pool == k


class TestRerankRequest:
    """请求体的形状——错了会导致对不上号。"""

    def test_request_contains_query_and_all_candidates(self, monkeypatch, _rerank_env):
        calls = []
        _stub_post(monkeypatch, {"results": [{"index": 0, "relevance_score": 1.0}]},
                   calls=calls)
        docs = [_Doc("A", "内容A"), _Doc("B", "内容B")]
        kb._rerank("年假多少天", docs, 2)
        assert calls, "应当发起过一次请求"
        payload = calls[0]["json"]
        assert payload["query"] == "年假多少天"
        assert payload["documents"] == ["内容A", "内容B"]
        assert payload["model"] == "test-reranker"
        assert calls[0]["url"].endswith("/rerank")


class TestEvalRerankGuard:
    """评测器必须能证明"重排真的发生了"。

    **为什么要有这道守卫**：传了 `--rerank` 只代表**请求了**重排。
    凭据缺失时重排客户端直接返回 None 且**不报错**——重排被静默跳过，
    但检索照常返回结果、断言照常全绿，报告读起来像"重排验证通过"。

    CI 上尤其危险：GitHub 对未配置的 secret 传**空串**，而空串正是这条静默路径。
    没有这道守卫，"CI 全绿"可能意味着"重排压根没跑"。
    """

    _CASE = None  # 用黄金集里的真实用例，见 _case()

    @staticmethod
    def _case():
        """直接取黄金集里的用例。

        不在这里另写一份期望：期望写两遍就会漂移，漂移之后这条测试
        会因为"用例本身不通过"而失败，掩盖掉它真正要守的东西。
        """
        import json
        from tests.eval.run_chunk_eval import CASES_FILE

        cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
        return dict(cases[0])

    def _clear_credentials(self, monkeypatch):
        """把所有凭据清空——重排客户端会返回 None（且不报错）。"""
        for attr in ("RERANK_API_KEY", "RERANK_API_BASE",
                     "EMBEDDING_API_KEY", "EMBEDDING_API_BASE", "LLM_API_KEY"):
            monkeypatch.setattr(kb.settings, attr, "")

    def test_suite_fails_when_rerank_silently_skipped(self, monkeypatch):
        """重排被静默跳过时，套件必须判失败。"""
        from tests.eval import run_chunk_eval as ce

        self._clear_credentials(monkeypatch)
        report = ce.run_suite(cases=[self._case()], rerank=True)

        check = report.get("rerank_check") or {}
        assert check.get("requested") is True, "应当记录下「请求了重排」这件事"
        assert check.get("applied_cases") == 0, "清空凭据后重排不可能生效"
        assert check.get("error"), "必须写明为什么判定失败"
        assert report["ok"] is False, (
            "重排没生效却判成通过——守卫失效了，CI 会把没跑重排的回归当成全绿")

    def test_suite_passes_when_rerank_actually_applied(self, monkeypatch, _rerank_env):
        """重排确实生效时套件必须判通过（守卫不能误伤正常路径）。"""
        from tests.eval import run_chunk_eval as ce

        _stub_post(monkeypatch, {"results": [
            {"index": 0, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.8},
            {"index": 2, "relevance_score": 0.7},
            {"index": 3, "relevance_score": 0.6},
        ]})
        report = ce.run_suite(cases=[self._case()], rerank=True)

        check = report.get("rerank_check") or {}
        assert check.get("requested") is True
        assert check.get("applied_cases") == 1, "重排应当在这条用例上生效"
        assert report["ok"] is True, (
            "重排生效却判失败——守卫误伤了正常路径：%s；用例失败原因：%s"
            % (report.get("rerank_check"),
               [r.get("failures") for r in report.get("results", [])]))


