"""Read-only knowledge-base inspection and retrieval API."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from app.api.auth import get_current_user
from app.rag.retriever import knowledge_retriever

router = APIRouter(prefix="/api/knowledge", tags=["知识库"])

class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=3, ge=1, le=10)

@router.get("/stats")
def knowledge_stats(_current_user=Depends(get_current_user)) -> dict:
    return knowledge_retriever.stats()

@router.post("/search")
def search_knowledge(request: SearchRequest, _current_user=Depends(get_current_user)) -> dict:
    return {"query": request.query, "results": knowledge_retriever.search(request.query, request.top_k)}
