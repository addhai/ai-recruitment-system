from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import List, Optional

from src.rag.knowledge_base import (
    query_knowledge_base_async, add_document, get_all_documents,
    delete_document,
)
from src.api.auth import get_current_user, require_all_authenticated, require_hr_admin
from src.safety.guard import InputGuard

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


class KnowledgeQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=50000)


@router.get("/documents")
def list_documents(current_user=Depends(require_all_authenticated)):
    return get_all_documents()


@router.post("/query")
async def query_knowledge(body: KnowledgeQuery, current_user=Depends(require_all_authenticated)):
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
async def add_new_document(body: DocumentCreate, current_user=Depends(require_hr_admin)):
    safe, reason = InputGuard.check(body.title + "\n" + body.content)
    if not safe:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"文档内容未通过安全检查：{reason}")
    # 在线程池中执行，避免向量写入阻塞事件循环
    import asyncio
    doc_id = await asyncio.to_thread(add_document, body.title, body.content, current_user.id)
    return {"message": "文档已添加并持久化", "id": doc_id, "title": body.title}


@router.delete("/documents/{doc_id}")
async def remove_document(doc_id: int, current_user=Depends(require_hr_admin)):
    """删除自定义文档并重建索引。

    自定义文档现已落库，因此提供对应的删除能力；
    内置的人事制度文档不在此表内，不受影响。
    """
    import asyncio
    from fastapi import HTTPException

    ok = await asyncio.to_thread(delete_document, doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"message": "文档已删除", "id": doc_id}
