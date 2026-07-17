from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    API_PORT: int = 8000
    API_HOST: str = "0.0.0.0"

    LLM_API_KEY: str = ""
    LLM_API_BASE: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"
    LLM_COMPLEX_MODEL: str = "deepseek-chat"
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_DIMENSIONS: int = 1024

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
