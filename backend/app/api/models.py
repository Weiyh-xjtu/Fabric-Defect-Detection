"""模型版本管理 API。"""

import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.core.logger import get_logger
from app.core.permissions import require_permission
from app.core.rbac import MODEL_MANAGE
from app.database.session import get_db
from app.entity.db_models import ModelVersion
from app.entity.schemas import ModelEvaluationRequest
from app.services.model_management_service import model_management_service
from app.training.training_service import training_service


logger = get_logger(__name__)
router = APIRouter(prefix="/api/models", tags=["模型管理"])


def _get_model_version(db: Session, model_version_id: int) -> ModelVersion:
    """获取模型版本或返回 404。"""
    item = (
        db.query(ModelVersion)
        .options(joinedload(ModelVersion.scene))
        .filter(ModelVersion.id == model_version_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="模型版本不存在")
    return item


@router.get("")
async def list_model_versions(
    scene_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """查看全部模型版本及文件、指标和使用次数。"""
    return {
        "items": model_management_service.list_model_versions(
            db,
            scene_id=scene_id,
            status=status,
        )
    }


@router.get("/current")
async def get_current_model(
    db: Session = Depends(get_db),
    _current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """查看当前全局检测模型。"""
    try:
        item = model_management_service.get_global_model_version(db)
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="尚未配置可用的全局检测模型")
    return model_management_service.serialize_model_version(db, item)


@router.post("/{model_version_id}/activate")
async def activate_model_version(
    model_version_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """切换全局检测模型；后续新检测任务立即使用该版本。"""
    try:
        item = model_management_service.activate_global_model(db, model_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "用户 %s 切换全局模型: model_version_id=%d, version=%s",
        current_user.username,
        item.id,
        item.version,
    )
    return {
        "message": f"全局检测模型已切换为 {item.version}",
        "model": model_management_service.serialize_model_version(db, item),
    }


@router.post("/{model_version_id}/test")
async def test_model_version(
    model_version_id: int,
    file: UploadFile = File(..., description="测试图片"),
    conf: float = Form(0.25, ge=0, le=1),
    iou: float = Form(0.45, ge=0, le=1),
    db: Session = Depends(get_db),
    _current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """使用指定版本测试图片，不影响全局选择，也不写检测历史。"""
    allowed_types = {"image/jpeg", "image/png", "image/bmp", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file.content_type}")
    item = _get_model_version(db, model_version_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传图片不能为空")

    suffix = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return model_management_service.predict_image(
            item,
            tmp_path,
            filename=file.filename,
            conf=conf,
            iou=iou,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        await file.close()


@router.post("/{model_version_id}/evaluate")
async def evaluate_model_version(
    model_version_id: int,
    request: ModelEvaluationRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """启动指定正式模型版本对应训练任务的后台评估。"""
    item = _get_model_version(db, model_version_id)
    if item.training_task_id is None:
        raise HTTPException(status_code=400, detail="该模型没有关联训练任务，无法定位评估数据集")
    request = request or ModelEvaluationRequest()
    result = training_service.start_validation(
        db=db,
        task_id=item.training_task_id,
        split=request.split,
        conf=request.conf,
        iou=request.iou,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info(
        "用户 %s 启动版本评估: model_version_id=%d, task_id=%d",
        current_user.username,
        item.id,
        item.training_task_id,
    )
    return {
        **result,
        "model_version_id": item.id,
        "model_version": item.version,
    }


@router.get("/{model_version_id}/evaluation")
async def get_model_evaluation_status(
    model_version_id: int,
    db: Session = Depends(get_db),
    _current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """查询指定模型版本的后台评估状态。"""
    item = _get_model_version(db, model_version_id)
    if item.training_task_id is None:
        return {
            "model_version_id": item.id,
            "model_version": item.version,
            "status": "unavailable",
            "error": "该模型没有关联训练任务，无法定位评估数据集",
        }
    return {
        **training_service.get_validation_status(item.training_task_id),
        "model_version_id": item.id,
        "model_version": item.version,
    }
