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
def list_model_versions(
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
def get_current_model(
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
def activate_model_version(
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


@router.post("/{model_version_id}/archive")
def archive_model_version(
    model_version_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """归档模型版本，保留本地权重、MinIO 备份和历史任务引用。"""
    try:
        item = model_management_service.archive_model(db, model_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("用户 %s 归档模型版本 %d", current_user.username, item.id)
    return {
        "message": f"模型版本 {item.version} 已归档",
        "model": model_management_service.serialize_model_version(db, item),
    }


@router.post("/{model_version_id}/unarchive")
def unarchive_model_version(
    model_version_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """将归档模型恢复为启用状态。"""
    try:
        item = model_management_service.unarchive_model(db, model_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("用户 %s 恢复归档模型版本 %d", current_user.username, item.id)
    return {
        "message": f"模型版本 {item.version} 已恢复为启用状态",
        "model": model_management_service.serialize_model_version(db, item),
    }


@router.post("/{model_version_id}/backup")
def backup_model_version(
    model_version_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """立即将模型本地权重备份到 MinIO。"""
    try:
        item = model_management_service.backup_model(db, model_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("MinIO 模型备份失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="模型备份服务暂时不可用") from exc
    logger.info("用户 %s 备份模型版本 %d", current_user.username, item.id)
    return {
        "message": f"模型版本 {item.version} 已完成备份",
        "model": model_management_service.serialize_model_version(db, item),
    }


@router.post("/{model_version_id}/restore")
def restore_model_version(
    model_version_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """从 MinIO 恢复模型到 backend/models，并校验文件完整性。"""
    item = _get_model_version(db, model_version_id)
    try:
        item = model_management_service.restore_model(db, item)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("MinIO 模型恢复失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="模型恢复服务暂时不可用") from exc
    logger.info("用户 %s 恢复模型版本 %d", current_user.username, item.id)
    return {
        "message": f"模型版本 {item.version} 已从备份恢复",
        "model": model_management_service.serialize_model_version(db, item),
    }


@router.delete("/{model_version_id}")
def delete_model_version(
    model_version_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """软删除模型，并清理正式导出副本与 MinIO 对象，保留训练任务产物。"""
    try:
        result = model_management_service.delete_model(db, model_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("模型删除失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"模型删除失败: {exc}") from exc
    item = result["model"]
    logger.info("用户 %s 删除模型版本 %d", current_user.username, item.id)
    return {
        "message": f"模型版本 {item.version} 已删除，训练任务产物未受影响",
        "local_action": result["local_action"],
        "minio_action": result["minio_action"],
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
            db,
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
def evaluate_model_version(
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
def get_model_evaluation_status(
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
        **training_service.get_validation_status(item.training_task_id, db=db),
        "model_version_id": item.id,
        "model_version": item.version,
    }
