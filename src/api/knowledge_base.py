from fastapi import APIRouter, Depends
from typing import List
from src.rag.knowledge_base import query_knowledge_base, add_document, get_all_documents
from src.api.auth import get_current_user

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


@router.get("/documents")
def list_documents(current_user=Depends(get_current_user)):
    return get_all_documents()


@router.post("/query")
def query_knowledge(query: str, current_user=Depends(get_current_user)):
    return query_knowledge_base(query)


@router.post("/documents")
def add_new_document(title: str, content: str, current_user=Depends(get_current_user)):
    add_document(title, content)
    return {"message": "Document added successfully", "title": title}
