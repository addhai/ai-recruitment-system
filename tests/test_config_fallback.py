# -*- coding: utf-8 -*-
"""配置口径：空串必须回退默认值，超时与重试次数按新的语义取值。

## 问题

环境变量设为**空串**会覆盖 pydantic 的默认值（实测：`RERANK_MODEL=""`
把 `BAAI/bge-reranker-v2-m3` 顶掉）。GitHub 对**未配置**的 Actions secret
传的就是空串，不是"不设置"——于是 `ci.yml` 里"RERANK_* 可选"原本是假的：
secret 没配时重排模型名会变成空字符串，重排请求必然失败。

对"可选配置"来说，空串应当等价于"没配"。

## 超时与重试

`LLM_TIMEOUT_SECONDS` 从 45 提到 90、`max_retries` 从硬编码 2 降到 1。
理由见 `tests/eval/README.md`：实测（507 次真实调用）
`parse_job_description` 的延迟是双峰的——p50 只有 4.2 秒，p90 却有 86.6 秒，
因为它要输出 8000+ tokens。45 秒的超时会把"只是慢、其实能成功"的调用
误判成失败（该环节实测失败率 2/73 = 2.74%，其它环节全是 0%），
而重试是全损重来：同一 prompt 在推理型模型上再次超时的概率很高，
前两次烧掉的 token 白花。
"""
from src.config import Settings, settings


class TestConfigEmptyStringFallback:
    """空串必须回退到默认值，不能把默认值顶掉"""

    def test_model_names_fall_back(self, monkeypatch):
        monkeypatch.setenv("RERANK_MODEL", "")
        monkeypatch.setenv("LLM_MODEL", "")
        monkeypatch.setenv("EMBEDDING_MODEL", "")
        s = Settings()
        assert s.RERANK_MODEL == "BAAI/bge-reranker-v2-m3"
        assert s.LLM_MODEL == "deepseek-flash"
        assert s.EMBEDDING_MODEL == "BAAI/bge-large-zh-v1.5"

    def test_api_base_falls_back(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "")
        monkeypatch.setenv("EMBEDDING_API_BASE", "")
        s = Settings()
        assert s.LLM_API_BASE.startswith("http")
        assert s.EMBEDDING_API_BASE.startswith("http")

    def test_api_key_keeps_empty_semantics(self, monkeypatch):
        """API Key 的默认值本来就是空，不能被这里改掉——
        它的"留空即复用 LLM_API_KEY"是显式语义，不是回退默认值。"""
        monkeypatch.setenv("EMBEDDING_API_KEY", "")
        s = Settings()
        assert s.EMBEDDING_API_KEY == ""

    def test_non_empty_value_is_kept(self, monkeypatch):
        """非空值当然不能被改"""
        monkeypatch.setenv("RERANK_MODEL", "my/reranker")
        s = Settings()
        assert s.RERANK_MODEL == "my/reranker"


class TestTimeoutAndRetry:
    """超时 90 秒（长输出任务不再被误杀）、重试 1 次（不再白烧一遍钱）"""

    def test_defaults(self):
        assert settings.LLM_TIMEOUT_SECONDS == 90
        assert settings.LLM_MAX_RETRIES == 1

    def test_client_uses_configured_retry_count(self, monkeypatch):
        """llm_json 构造客户端时用的是配置值，不是硬编码的 2"""
        from src.services import llm_config as lc
        from src.services import llm_json as lj

        captured = {}

        class _FakeLLM:
            def __init__(self, **kw):
                captured.update(kw)

        monkeypatch.setattr(lj, "ChatOpenAI", _FakeLLM)
        monkeypatch.setattr(lj, "_llm_cache", {})
        # llm_json 是在函数体内 `from src.services import llm_config`，
        # 绑定发生在调用时，所以桩要打在 llm_config 模块的属性上，
        # 打 llm_json 的模块级属性会 AttributeError（那上面根本没有它）。
        monkeypatch.setattr(lc, "get_effective_config", lambda: {
            "model": "m", "base_url": "http://x", "api_key": "k",
            "timeout_seconds": 90, "temperature": 0.1,
        })

        lj.get_llm()
        assert captured["max_retries"] == settings.LLM_MAX_RETRIES
        assert captured["timeout"] == 90
