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
    # Embedding 独立配置：DeepSeek 不提供 embedding 接口，默认走硅基流动（OpenAI 兼容）
    # EMBEDDING_API_KEY 留空时回退复用 LLM_API_KEY（适用于同一网关同时提供 chat+embedding 的场景）
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_BASE: str = "https://api.siliconflow.cn/v1"
    EMBEDDING_MODEL: str = "BAAI/bge-large-zh-v1.5"
    EMBEDDING_DIMENSIONS: int = 1024

    @property
    def embedding_api_key(self) -> str:
        return self.EMBEDDING_API_KEY or self.LLM_API_KEY

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

    # 简历上传后是否自动启动 AI 工作流（自动化中台开关，关闭后需手动点"启动AI评估"）
    AUTO_START_WORKFLOW: bool = True

    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
