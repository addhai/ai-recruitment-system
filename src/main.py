from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api import auth, candidates, interviews, questionnaires, evaluations, talent_pool, dashboard, sse, knowledge_base
from src.models.database import init_db

app = FastAPI(title="AI招聘系统", version="1.0.0", description="基于AI的智能招聘管理系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def root():
    return {"message": "AI招聘系统 API", "version": "1.0.0"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
