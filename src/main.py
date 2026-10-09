from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.logging_setup import setup_logging
from src.api import auth, candidates, interviews, questionnaires, evaluations, talent_pool, dashboard, sse, knowledge_base
from src.api import job_descriptions, reviews, positions, llm_stats, llm_config
from src.models.database import init_db
from src.config import settings

setup_logging(settings.LOG_LEVEL)

def _docs_kwargs() -> dict:
    """交互式 API 文档的 FastAPI 参数。

    默认开启（本地开发/联调需要），公网部署请设 ENABLE_API_DOCS=false：
    /docs 与 /openapi.json 会把全部端点、参数与数据模型公开可读，
    对攻击者等于一张免费的地图。注意本地脚本（冒烟/评测）会读 /openapi.json。

    抽成函数是为了可测——否则只能 reload 整个模块才能验证这个开关。
    """
    if settings.ENABLE_API_DOCS:
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


app = FastAPI(title="AI招聘系统", version="1.0.0",
              description="基于AI的智能招聘管理系统", **_docs_kwargs())

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(candidates.router)
app.include_router(interviews.router)
app.include_router(questionnaires.router)
app.include_router(evaluations.router)
app.include_router(talent_pool.router)
app.include_router(dashboard.router)
app.include_router(sse.router)
app.include_router(knowledge_base.router)
app.include_router(job_descriptions.router)
app.include_router(positions.router)
app.include_router(reviews.router)
app.include_router(llm_stats.router)
app.include_router(llm_config.router)


@app.on_event("startup")
def startup():
    init_db()


@app.on_event("shutdown")
async def shutdown():
    # postgres checkpointer 用的是连接池，退出时不关会留下悬挂连接
    from src.workflow import runner
    await runner.close_graph()


@app.get("/")
def root():
    return {"message": "AI招聘系统 API", "version": "1.0.0"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)
