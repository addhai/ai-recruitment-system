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

    # 文化契合三段判定阈值。
    # 此前只有 60 一档一票否决，而文化在综合分里仅占 5%——同一份简历实测
    # 在 58/72 之间反复横跳，结论直接翻转。改为区间判定后，边缘分数不再
    # 直接淘汰，而是打标记继续走完流程，交给 HR 事后批量裁决。
    CULTURE_PASS_THRESHOLD: int = 60      # >= 直接通过
    CULTURE_REVIEW_THRESHOLD: int = 50    # [50, 60) 进入人工复核；< 50 直接淘汰

    # 评分口径版本：提示词与权重变更会导致新评分与历史不可比，
    # 落进 WorkflowRun.results 便于回溯。
    SCORING_VERSION: str = "v2"

    # ---------------------------------------------------------------- LLM 成本
    # 单价按供应商报价配置（单位：美元/百万 token）。留 0 表示未配置成本价，
    # 此时 cost 记 0 且**自动跳过预算检查**——避免忘配单价就把业务卡死。
    LLM_INPUT_PRICE_PER_MILLION: float = 0.0
    LLM_OUTPUT_PRICE_PER_MILLION: float = 0.0

    # 调用日志保留天数；init_db() 启动时按此清理过期的 llm_call_logs
    LLM_LOG_RETENTION_DAYS: int = 90

    # ---------------------------------------------------------------- LLM 预算
    # 超限时停止后续调用，工作流转「待人工评估」，不产出半成品招聘决策。
    LLM_BUDGET_ENABLED: bool = True
    LLM_BUDGET_PERIOD: str = "daily"          # daily | monthly
    LLM_BUDGET_USD: float = 5.0
    LLM_BUDGET_ACTION: str = "halt"           # halt=停转人工 / warn=仅告警继续
    # 每累计 N 次调用回读一次数据库，避免每次都 SUM 全表
    LLM_BUDGET_REFRESH_EVERY: int = 20

    # 候选���带"待人工复核"标记时是否跳过第三轮面试。
    # 该标记出现的前提就是文化契合落在边缘区间、结论本就不稳；
    # 继续花一轮面试的成本去确认一个大概率被复核推翻的结论并不划算。
    # 注意：跳过后面试均分只由前两轮构成，综合分中面试权重(35%)保持不变，
    # 最终档位仍锁定在"待人工复核"，不会因此被自动录用。
    SKIP_ROUND3_ON_REVIEW: bool = False

    # "不限制"的识别口径：JD 里学历/年限/价值观可以显式标为不限制；
    # JD 没写同样按不限制处理——两种情况都不该拿它去卡候选人，
    # 否则等于凭空造出候选人不满足的要求。
    UNCONSTRAINED_MARKERS: list[str] = ["不限制", "无", "不限", "无要求", "不设限"]

    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
