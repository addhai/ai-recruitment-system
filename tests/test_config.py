"""配置项测试：端口、CORS 白名单解析、Embedding 配置化。"""
from src.config import settings


def test_api_port_is_8000():
    assert settings.API_PORT == 8000


def test_cors_origins_is_list():
    assert isinstance(settings.CORS_ORIGINS, list)
    # 至少包含本地开发地址
    assert any("localhost" in o for o in settings.CORS_ORIGINS)


def test_embedding_model_configurable():
    # Phase 2 已去除硬编码，应能从配置读取且非空
    assert settings.EMBEDDING_MODEL
    assert isinstance(settings.EMBEDDING_MODEL, str)
