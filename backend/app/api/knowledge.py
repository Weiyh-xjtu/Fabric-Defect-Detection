"""Read-only knowledge-base inspection and retrieval API."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.api.auth import get_current_user
from app.rag.retriever import knowledge_retriever

router = APIRouter(prefix="/api/knowledge", tags=["知识库"])

class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=3, ge=1, le=10)

@router.post("/build")
def build_knowledge(_current_user=Depends(get_current_user)) -> dict:
    try:
        return knowledge_retriever.build()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"知识库索引构建失败：{exc}") from exc

@router.get("/stats")
def knowledge_stats(_current_user=Depends(get_current_user)) -> dict:
    return knowledge_retriever.stats()

@router.post("/search")
def search_knowledge(request: SearchRequest, _current_user=Depends(get_current_user)) -> dict:
    retrieval = knowledge_retriever.retrieve(request.query, request.top_k)
    return {
        "query": request.query,
        "mode": retrieval["mode"],
        "fallback_reason": retrieval["fallback_reason"],
        "results": retrieval["results"],
    }
