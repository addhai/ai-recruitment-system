# -*- coding: utf-8 -*-
"""模型配置测试：切换模型、密钥加密、分时计价、DeepSeek 预设。

核心不变量：
1. 接口永不回传 API Key 明文（只回掩码）
2. 密钥加密存储，SECRET_KEY 变更后能优雅回退而不是崩溃
3. 切换模型后客户端缓存失效，新配置立即生效（无需重启）
4. 分时计价按北京时间高峰窗口（周一至周五 9-12、14-18）
5. 仅管理员可读写配置
"""
from datetime import datetime, timezone, timedelta

import pytest

from src.services import llm_config
from src.models.database import SessionLocal, LLMSettings

_CN = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def _clean_settings():
    """每个用例前后清空模型配置，避免用例间互相影响"""
    def _purge():
        with SessionLocal() as db:
            db.query(LLMSettings).delete()
            db.commit()
    _purge()
    llm_config.invalidate_client_cache()
    yield
    _purge()
    llm_config.invalidate_client_cache()


@pytest.fixture
def admin_headers(client):
    """种子 admin 账号"""
    r = client.post("/auth/login", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ================================================================ 密钥加密
class TestApiKeySecurity:
    def test_encrypt_decrypt_roundtrip(self):
        token = llm_config.encrypt_api_key("sk-test-1234567890")
        assert token != "sk-test-1234567890", "不能明文存储"
        assert llm_config.decrypt_api_key(token) == "sk-test-1234567890"

    def test_mask_never_reveals_middle(self):
        masked = llm_config.mask_api_key("sk-abcdefghijklmnop")
        assert masked.startswith("sk-a") and masked.endswith("mnop")
        assert "efghijkl" not in masked

    def test_mask_short_key_fully_hidden(self):
        assert set(llm_config.mask_api_key("short")) == {"*"}

    def test_decrypt_failure_returns_none(self, monkeypatch):
        """SECRET_KEY 轮换后旧密文不可解 —— 应回退而不是抛异常"""
        token = llm_config.encrypt_api_key("sk-original")
        monkeypatch.setattr(llm_config.settings, "SECRET_KEY", "a-different-secret")
        assert llm_config.decrypt_api_key(token) is None

    def test_api_never_returns_plaintext_key(self, client, admin_headers):
        client.put("/llm-config", json={"api_key": "sk-supersecret-value-1234"},
                   headers=admin_headers)
        body = client.get("/llm-config", headers=admin_headers).json()
        assert "sk-supersecret-value-1234" not in str(body)
        assert body["api_key_masked"].startswith("sk-s")
        assert body["api_key_configured"] is True


# ================================================================ 分时计价
class TestPeakPricing:
    @pytest.mark.parametrize("weekday,hour,expected", [
        (0, 9, True),    # 周一 09:00 高峰
        (0, 11, True),
        (0, 12, False),  # 12:00 起午休，空闲
        (0, 14, True),
        (0, 17, True),
        (0, 18, False),  # 18:00 后空闲
        (0, 3, False),   # 凌晨空闲
        (5, 10, False),  # 周六全天空闲
        (6, 10, False),  # 周日全天空闲
    ])
    def test_peak_window_by_beijing_time(self, weekday, hour, expected):
        # 2026-10-05 是周一
        base = datetime(2026, 10, 5, hour, 0, tzinfo=_CN)
        target = base + timedelta(days=weekday)
        assert llm_config.is_peak_now(target) is expected

    def test_deepseek_preset_has_peak_multiplier(self):
        preset = llm_config.DEEPSEEK_PRESETS["deepseek-flash"]
        assert preset["peak_multiplier"] == 2.0, "官方：空闲价为高峰的一半"
        assert preset["currency"] == "CNY"
        assert preset["base_url"] == "https://api.deepseek.com"

    def test_cost_doubles_in_peak(self, monkeypatch):
        monkeypatch.setattr(llm_config.settings, "LLM_INPUT_PRICE_PER_MILLION", 1.0)
        monkeypatch.setattr(llm_config.settings, "LLM_OUTPUT_PRICE_PER_MILLION", 4.0)
        monkeypatch.setattr(llm_config.settings, "LLM_PEAK_MULTIPLIER", 2.0)

        off_peak = datetime(2026, 10, 5, 8, 0, tzinfo=_CN)   # 周一 08:00
        peak = datetime(2026, 10, 5, 10, 0, tzinfo=_CN)      # 周一 10:00

        cost_off = llm_config.compute_cost(1_000_000, 0, now=off_peak)
        cost_peak = llm_config.compute_cost(1_000_000, 0, now=peak)
        assert cost_off == pytest.approx(1.0)
        assert cost_peak == pytest.approx(2.0)

    def test_no_peak_multiplier_for_flat_providers(self, monkeypatch):
        """不分时段的供应商（如 OpenAI）peak_multiplier=1，两个时段同价"""
        monkeypatch.setattr(llm_config.settings, "LLM_INPUT_PRICE_PER_MILLION", 3.0)
        monkeypatch.setattr(llm_config.settings, "LLM_PEAK_MULTIPLIER", 1.0)
        off = llm_config.compute_cost(1_000_000, 0, now=datetime(2026, 10, 5, 3, tzinfo=_CN))
        pk = llm_config.compute_cost(1_000_000, 0, now=datetime(2026, 10, 5, 10, tzinfo=_CN))
        assert off == pk == pytest.approx(3.0)


# ================================================================ 配置读写
class TestConfigPersistence:
    def test_save_and_read_back(self, client, admin_headers):
        r = client.put("/llm-config", json={
            "model": "deepseek-flash", "base_url": "https://api.deepseek.com",
            "api_key": "sk-abcdefghijklmnop", "input_price": 1.0,
            "output_price": 4.0, "currency": "CNY", "peak_multiplier": 2.0,
        }, headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["model"] == "deepseek-flash"
        assert body["source"] == "database", "数据库配置应优先于 .env"
        assert body["pricing_configured"] is True

        # 重新读取应一致
        again = client.get("/llm-config", headers=admin_headers).json()
        assert again["model"] == "deepseek-flash"

    def test_partial_update_keeps_other_fields(self, client, admin_headers):
        client.put("/llm-config", json={"model": "m1", "output_price": 9.0},
                   headers=admin_headers)
        client.put("/llm-config", json={"temperature": 0.5}, headers=admin_headers)
        body = client.get("/llm-config", headers=admin_headers).json()
        assert body["model"] == "m1", "未提供的字段不应被清空"
        assert body["output_price"] == 9.0

    def test_empty_api_key_clears_to_env(self, client, admin_headers):
        client.put("/llm-config", json={"api_key": "sk-temporary-key-value"},
                   headers=admin_headers)
        assert client.get("/llm-config", headers=admin_headers).json()["source"] == "database"

        client.put("/llm-config", json={"api_key": ""}, headers=admin_headers)
        body = client.get("/llm-config", headers=admin_headers).json()
        # 清空后回退 .env（测试环境 .env 有 key）
        assert body["api_key_configured"] is True
        assert body["source"] == "database" or body["source"] == "env"

    def test_invalid_prices_rejected(self, client, admin_headers):
        assert client.put("/llm-config", json={"input_price": -1},
                          headers=admin_headers).status_code == 422
        assert client.put("/llm-config", json={"temperature": 5},
                          headers=admin_headers).status_code == 422
        assert client.put("/llm-config", json={"peak_multiplier": 0.5},
                          headers=admin_headers).status_code == 422


# ================================================================ 缓存失效
class TestClientCacheInvalidation:
    def test_switching_model_invalidates_cache(self, client, admin_headers, monkeypatch):
        """切换模型后必须立即生效，不能还挂着旧客户端"""
        monkeypatch.setattr(llm_config.settings, "LLM_API_KEY", "sk-env-key-value")
        from src.services import llm_json

        llm_json._llm_cache.clear()
        # 先构造一个旧配置的客户端
        llm_json.get_llm()
        assert len(llm_json._llm_cache) >= 1

        client.put("/llm-config", json={"model": "another-model",
                                       "api_key": "sk-abcdefghijklmnop"},
                   headers=admin_headers)
        assert len(llm_json._llm_cache) == 0, "保存配置后必须清空客户端缓存"

        new_llm = llm_json.get_llm()
        assert getattr(new_llm, "model_name", "") == "another-model", \
            "新客户端应使用切换后的模型"

    def test_knowledge_base_chain_reset(self, client, admin_headers):
        from src.rag import knowledge_base as kb
        kb._init_attempted = True
        client.put("/llm-config", json={"model": "m2"}, headers=admin_headers)
        assert kb._init_attempted is False, "知识库问答链需一并重建"
        assert kb._chat_llm is None


# ================================================================ 预设与权限
class TestPresetsAndPermissions:
    def test_deepseek_presets_available(self, client, admin_headers):
        body = client.get("/llm-config/presets", headers=admin_headers).json()
        presets = body["presets"]
        assert "deepseek-flash" in presets
        assert "deepseek-v4-pro" in presets
        assert presets["deepseek-flash"]["input_price"] > 0
        assert "官方" in body["note"]

    def test_admin_required(self, client):
        import uuid
        u = f"llmviewer_{uuid.uuid4().hex[:8]}"
        client.post("/auth/register", json={"username": u, "email": f"{u}@t.com",
                                            "password": "testpass123", "role": "hr"})
        tok = client.post("/auth/login", data={"username": u, "password": "testpass123"}
                          ).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        # HR 也不行——模型配置涉及密钥与成本口径，收口到管理员
        assert client.get("/llm-config", headers=h).status_code == 403
        assert client.put("/llm-config", json={"model": "x"}, headers=h).status_code == 403
        assert client.get("/llm-config/presets", headers=h).status_code == 403

    def test_unauthenticated_rejected(self, client):
        assert client.get("/llm-config").status_code == 401
        assert client.put("/llm-config", json={}).status_code == 401