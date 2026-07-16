"""Knowledge-base file management, inspection, and retrieval API.

任何登录用户都可增删知识库文件；文件变动后自动触发后台向量索引重建。
"""
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.rag.document_loader import ALLOWED_KB_EXTENSIONS
from app.rag.retriever import knowledge_retriever

logger = get_logger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["知识库"])

# 单个知识库文档大小上限，防止超大文件拖垮 embedding 与内存。
MAX_KB_FILE_SIZE = 20 * 1024 * 1024


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=3, ge=1, le=10)


def _knowledge_dir() -> Path:
    """从检索器读取知识库目录（便于测试 monkeypatch），并确保目录存在。"""
    directory = knowledge_retriever.knowledge_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe_target(directory: Path, filename: str) -> Path:
    """把用户提供的文件名解析为目录内的安全绝对路径，拒绝路径穿越。"""
    cleaned = Path(filename or "").name
    if not cleaned:
        raise HTTPException(status_code=400, detail="文件名无效")
    target = (directory / cleaned).resolve()
    root = directory.resolve()
    try:
        common = os.path.commonpath([str(root), str(target)])
    except ValueError:
        common = ""
    if common != str(root):
        raise HTTPException(status_code=400, detail="文件路径不在知识库目录中")
    return target


def _file_entry(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "ext": path.suffix.lower(),
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def _list_files(directory: Path) -> list[dict]:
    if not directory.exists():
        return []
    entries = [
        _file_entry(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.suffix.lower() in ALLOWED_KB_EXTENSIONS
    ]
    return entries


@router.get("/files")
def list_knowledge_files(_current_user=Depends(get_current_user)) -> dict:
    directory = _knowledge_dir()
    return {"directory": str(directory), "files": _list_files(directory)}


@router.post("/files")
async def upload_knowledge_files(
    files: list[UploadFile] = File(...),
    _current_user=Depends(get_current_user),
) -> dict:
    """上传一个或多个知识库文档（pdf/md/txt）。同名文件直接覆盖，随后自动重建。"""
    upload_files = list(files)
    if not upload_files:
        raise HTTPException(status_code=400, detail="请选择要上传的文件")

    directory = _knowledge_dir()
    uploaded: list[dict] = []
    written_paths: list[Path] = []
    try:
        for upload_file in upload_files:
            original_name = Path(upload_file.filename or "").name
            if not original_name:
                raise HTTPException(status_code=400, detail="文件名无效")
            ext = Path(original_name).suffix.lower()
            if ext not in ALLOWED_KB_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"仅支持 {'/'.join(sorted(ALLOWED_KB_EXTENSIONS))} 文件：{original_name}",
                )
            content = await upload_file.read()
            if len(content) > MAX_KB_FILE_SIZE:
                raise HTTPException(status_code=413, detail=f"{original_name} 超过 20MB 大小限制")

            target = _safe_target(directory, original_name)
            with open(target, "wb") as output:  # 同名覆盖
                output.write(content)
            written_paths.append(target)
            uploaded.append(_file_entry(target))
    except HTTPException:
        # 单个文件校验失败时回滚本次已写入的文件，避免半成品入库。
        for path in written_paths:
            try:
                path.unlink()
            except OSError:
                pass
        raise

    logger.info("知识库上传 %d 个文件，触发重建", len(uploaded))
    rebuild = knowledge_retriever.schedule_rebuild()
    return {"uploaded": uploaded, "rebuild": rebuild}


@router.delete("/files/{filename}")
def delete_knowledge_file(filename: str, _current_user=Depends(get_current_user)) -> dict:
    """删除指定知识库文件（允许删除任意文件，含内置文档），随后自动重建。"""
    directory = _knowledge_dir()
    target = _safe_target(directory, filename)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        target.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"删除失败：{exc}") from exc

    logger.info("知识库删除文件 %s，触发重建", target.name)
    rebuild = knowledge_retriever.schedule_rebuild()
    return {"deleted": target.name, "rebuild": rebuild}


@router.post("/rebuild", status_code=202)
def rebuild_knowledge(_current_user=Depends(get_current_user)) -> dict:
    """手动触发一次后台向量索引重建（去抖，不会重复排队）。"""
    return knowledge_retriever.schedule_rebuild()


@router.post("/build")
def build_knowledge(_current_user=Depends(get_current_user)) -> dict:
    """同步构建向量索引（向后兼容旧调用方）。"""
    try:
        return knowledge_retriever.build()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"知识库索引构建失败：{exc}") from exc


@router.get("/stats")
def knowledge_stats(_current_user=Depends(get_current_user)) -> dict:
    stats = knowledge_retriever.stats()
    stats["rebuild"] = knowledge_retriever.rebuild_status()
    return stats


@router.post("/search")
def search_knowledge(request: SearchRequest, _current_user=Depends(get_current_user)) -> dict:
    retrieval = knowledge_retriever.retrieve(request.query, request.top_k)
    return {
        "query": request.query,
        "mode": retrieval["mode"],
        "fallback_reason": retrieval["fallback_reason"],
        "results": retrieval["results"],
    }
