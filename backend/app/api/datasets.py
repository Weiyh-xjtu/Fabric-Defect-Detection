"""数据集管理 API 路由。

接口列表（均需模型管理权限）：
  - GET    /api/datasets                      数据集列表（含归属场景与类别）
  - PUT    /api/datasets/{name}/names         修改显示名与类别中文名（双写 yaml+DB）
  - POST   /api/datasets/upload               上传 zip 暂存并解析（第一段）
  - POST   /api/datasets/upload/{id}/commit   确认落盘并登记场景（第二段）
  - POST   /api/datasets/{name}/evaluate      数据集体检评估（缓存报告）
"""

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.core.permissions import require_permission
from app.core.rbac import MODEL_MANAGE
from app.database.session import get_db
from app.services.dataset_service import dataset_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/datasets", tags=["数据集管理"])

VALID_CATEGORIES = {"agriculture", "industry", "remote_sensing", "medical", "traffic"}


class UpdateNamesRequest(BaseModel):
    """中文名修改请求；英文类别名不在此接口的可改范围内。"""

    display_name: str | None = Field(None, max_length=100)
    class_names_cn: dict[str, str] = Field(default_factory=dict)


class CommitUploadRequest(BaseModel):
    """上传确认请求；class_names 是英文名唯一可修改的时机。"""

    scene_name: str = Field(..., min_length=2, max_length=50)
    display_name: str = Field(..., min_length=1, max_length=100)
    category: str = Field("industry")
    class_names: list[str] = Field(..., min_length=1)
    class_names_cn: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    overwrite_classes: bool = False


@router.get("", summary="数据集列表")
async def list_datasets(
    db: Session = Depends(get_db),
    _current_user=Depends(require_permission(MODEL_MANAGE)),
):
    return {"items": dataset_service.list_datasets(db)}


@router.put("/{dataset_name}/names", summary="修改显示名与类别中文名")
async def update_dataset_names(
    dataset_name: str,
    request: UpdateNamesRequest,
    db: Session = Depends(get_db),
    _current_user=Depends(require_permission(MODEL_MANAGE)),
):
    try:
        return dataset_service.update_names(
            db,
            dataset_name,
            display_name=request.display_name,
            class_names_cn=request.class_names_cn,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/upload", summary="上传数据集包（暂存解析）")
async def upload_dataset(
    file: UploadFile = File(...),
    _current_user=Depends(require_permission(MODEL_MANAGE)),
):
    content = await file.read()
    try:
        return await asyncio.to_thread(
            dataset_service.stage_upload, content, file.filename or "dataset.zip"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/upload/{upload_id}/commit", summary="确认上传并登记场景")
async def commit_dataset_upload(
    upload_id: str,
    request: CommitUploadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    if request.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"无效场景分类：{request.category}")
    try:
        return await asyncio.to_thread(
            dataset_service.commit_upload,
            db,
            upload_id,
            scene_name=request.scene_name,
            display_name=request.display_name,
            category=request.category,
            class_names=request.class_names,
            class_names_cn=request.class_names_cn,
            description=request.description,
            user_id=current_user.id,
            overwrite_classes=request.overwrite_classes,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{dataset_name}/evaluate", summary="数据集体检评估")
async def evaluate_dataset(
    dataset_name: str,
    force: bool = False,
    db: Session = Depends(get_db),
    _current_user=Depends(require_permission(MODEL_MANAGE)),
):
    try:
        return await asyncio.to_thread(dataset_service.evaluate, db, dataset_name, force=force)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
