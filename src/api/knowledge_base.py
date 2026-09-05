from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import List, Optional

from src.rag.knowledge_base import (
    query_knowledge_base_async, add_document, get_all_documents,
)
from src.api.auth import get_current_user
from src.safety.guard import InputGuard

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


class KnowledgeQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=50000)


@router.get("/documents")
def list_documents(current_user=Depends(get_current_user)):
    return get_all_documents()


@router.post("/query")
async def query_knowledge(body: KnowledgeQuery, current_user=Depends(get_current_user)):
    # 输入护栏：拦截提示注入与危险内容
    safe, reason = InputGuard.check(body.query)
    if not safe:
        return {
            "answer": f"您的问题包含不安全内容，已被安全策略拦截（{reason}）。请重新表述您的人事制度相关问题。",
            "sources": [],
            "mode": "blocked",
        }
    result = await query_knowledge_base_async(body.query)
    return result


@router.post("/documents")
async def add_new_document(body: DocumentCreate, current_user=Depends(get_current_user)):
    safe, reason = InputGuard.check(body.title + "\n" + body.content)
    if not safe:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"文档内容未通过安全检查：{reason}")
    # 在线程池中执行，避免向量写入阻塞事件循环
    import asyncio
    await asyncio.to_thread(add_document, body.title, body.content)
    return {"message": "Document added successfully", "title": body.title}
