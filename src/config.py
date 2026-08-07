from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    API_PORT: int = 8000
    API_HOST: str = "0.0.0.0"
    # CORS 白名单：生产环境通过环境变量 CORS_ORIGINS 覆盖（逗号分隔）
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]

    LLM_API_KEY: str = ""
    LLM_API_BASE: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"
    LLM_COMPLEX_MODEL: str = "deepseek-chat"
    # Embedding 模型：必须与 embedding 提供方一致（维度随之匹配）
    # 若使用 OpenAI 兼容端点，常见为 text-embedding-3-small(1536) / text-embedding-3-large(3072)
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536

    DATABASE_URL: str = "sqlite:///./recruitment.db"
    SQLITE_URL: str = "sqlite:///./recruitment.db"

    REDIS_URL: Optional[str] = None

    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION_NAME: str = "recruitment_knowledge"
    VECTOR_STORE_BACKEND: str = "chroma"

    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET_DOCS: str = "documents"
    MINIO_BUCKET_LOGS: str = "logs"
    MINIO_BUCKET_MODELS: str = "models"

    RABBITMQ_URL: Optional[str] = None

    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    JWT_SECRET_KEY: str = "your-jwt-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    # 飞书集成
    FEISHU_APP_ID: Optional[str] = None
    FEISHU_APP_SECRET: Optional[str] = None
    FEISHU_WEBHOOK_URL: Optional[str] = None  # 机器人webhook

    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
