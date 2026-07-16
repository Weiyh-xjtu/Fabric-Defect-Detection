"""
训练相关 API 路由

接口列表：
  - GET    /api/training/scenes              获取可用检测场景
  - POST   /api/training/start               启动训练任务
  - GET    /api/training/tasks               获取训练任务列表
  - GET    /api/training/status/{task_id}    获取训练状态（含最新指标）
  - GET    /api/training/metrics/{task_id}   获取训练指标历史（所有 epoch）
  - POST   /api/training/stop/{task_id}      停止训练任务
  - GET    /api/training/results/{task_uuid}  获取 results.csv 原始数据
"""

import base64
import os
import tempfile

import cv2
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.permissions import require_permission
from app.core.rbac import MODEL_MANAGE
from app.config.settings import settings
from app.core.logger import get_logger
from app.database.session import get_db
from app.entity.db_models import DetectionScene, TrainingTask
from app.entity.schemas import (
    ModelExportRequest,
    ModelExportResponse,
    ModelValidateRequest,
    ModelValidateResponse,
    TrainingMetricResponse,
    TrainingTaskCreate,
    TrainingTaskResponse,
)
from app.training.training_service import training_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/training", tags=["模型训练"])


def _get_owned_task(db: Session, task_id: int, _user) -> TrainingTask:
    """获取训练任务；调用方已通过模型管理权限校验。"""
    task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    return task


@router.get("/scenes")
async def list_training_scenes(
    db: Session = Depends(get_db),
    _current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """获取可用于训练的启用场景。"""
    scenes = (
        db.query(DetectionScene)
        .filter(DetectionScene.is_active.is_(True))
        .order_by(DetectionScene.id.asc())
        .all()
    )
    return {
        "items": [
            {
                "id": scene.id,
                "name": scene.name,
                "display_name": scene.display_name,
            }
            for scene in scenes
        ]
    }


@router.post("/start")
async def start_training(
    request: TrainingTaskCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """
    启动模型训练任务

    - **scene_id**: 关联的检测场景 ID
    - **model_name**: 基础模型（yolov11n/s/m/l/x）
    - **epochs**: 训练轮数（10~500）
    - **batch_size**: 批次大小（1~64）
    - **device**: 训练设备（cpu / 0 / 1）
    - **optimizer**: 优化器（SGD / Adam / AdamW）
    - **lr0**: 初始学习率
    - **augment_config**: 数据增强配置（JSON）
    """
    # ── 构造训练配置 ──
    config = {
        "model_name": request.model_name,
        "epochs": request.epochs,
        "img_size": request.img_size,
        "batch_size": request.batch_size,
        "device": request.device,
        "optimizer": request.optimizer,
        "lr0": request.lr0,
        "augment_config": request.augment_config,
    }

    # ── 从场景获取数据集路径 ──
    from app.entity.db_models import DetectionScene
    scene = db.query(DetectionScene).filter(DetectionScene.id == request.scene_id).first()
    if not scene:
        raise HTTPException(status_code=404, detail="检测场景不存在")

    # 尝试查找数据集路径（约定：backend/datasets/{场景名}/yolo_dataset/）
    # __file__ = .../backend/app/api/training.py
    # 需要向上3层到达 backend/
    api_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(api_dir)
    backend_dir = os.path.dirname(app_dir)
    dataset_path = os.path.join(
        backend_dir,
        "datasets",
        scene.name,
        "yolo_dataset",
    )
    logger.info(f"backend目录: {backend_dir}")
    logger.info(f"数据集路径: {dataset_path}")
    config["dataset_path"] = dataset_path

    # 检查 data.yaml 是否存在
    data_yaml = os.path.join(dataset_path, "data.yaml")
    if os.path.exists(data_yaml):
        config["data_yaml"] = data_yaml
    else:
        raise HTTPException(
            status_code=400,
            detail=f"data.yaml 不存在：{data_yaml}，请先完成数据集准备",
        )

    # ── 启动训练 ──
    try:
        task = training_service.start_training(
            db=db,
            user_id=current_user.id,
            scene_id=request.scene_id,
            config=config,
        )
    except Exception as e:
        logger.error("启动训练失败：%s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"启动训练失败：{str(e)}")

    logger.info(
        "用户 %s 启动训练任务：scene=%s, model=%s, epochs=%d",
        current_user.username, scene.name, request.model_name, request.epochs,
    )

    return {
        "id": task.id,
        "task_uuid": task.task_uuid,
        "status": task.status,
        "model_name": task.model_name,
        "epochs": task.epochs,
        "message": "训练任务已创建，正在后台启动",
    }


@router.get("/tasks")
async def list_training_tasks(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """获取当前用户的训练任务列表"""
    tasks = training_service.get_task_list(db, user_id=None)
    return {"total": len(tasks), "items": tasks}


@router.post("/rescan")
async def rescan_training_tasks(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """扫描训练产物目录，将磁盘上存在但数据库缺失的训练任务恢复入库。

    用于数据库重建后从 backend/runs/train 目录恢复历史训练记录，
    恢复后即可正常查看历史、评估、导出与下载模型。
    """
    result = training_service.rescan_tasks(db)
    logger.info(
        "用户 %s 触发训练历史恢复：恢复 %d 条，跳过 %d 条，失败 %d 条",
        current_user.username,
        result["recovered"],
        result["skipped"],
        result["failed"],
    )
    return result


@router.get("/status/{task_id}")
async def get_training_status(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """
    获取训练任务状态

    返回任务基本信息、当前进度和最新 epoch 指标
    前端可轮询此接口实现实时监控
    """
    _get_owned_task(db, task_id, current_user)
    status = training_service.get_training_status(db, task_id)
    if "error" in status:
        raise HTTPException(status_code=404, detail=status["error"])
    return status


@router.get("/metrics/{task_id}")
async def get_training_metrics(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """
    获取训练任务的所有 epoch 指标

    用于绘制完整的训练曲线（loss、mAP、precision、recall）
    """
    _get_owned_task(db, task_id, current_user)
    metrics = training_service.get_training_metrics(db, task_id)
    return {"task_id": task_id, "total": len(metrics), "metrics": metrics}


@router.post("/stop/{task_id}")
async def stop_training(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """停止正在运行的训练任务"""
    _get_owned_task(db, task_id, current_user)
    result = training_service.stop_training(db, task_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/results/{task_uuid}")
async def get_results_csv(
    task_uuid: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """获取训练任务的 Ultralytics results.csv 文件。"""
    task = (
        db.query(TrainingTask)
        .filter(TrainingTask.task_uuid == task_uuid)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="训练任务不存在")

    results_path = os.path.join(
        settings.TRAIN_OUTPUT_DIR,
        f"task_{task_uuid}",
        "results.csv",
    )
    if not os.path.exists(results_path):
        raise HTTPException(status_code=404, detail="results.csv 文件不存在")

    return FileResponse(
        path=results_path,
        media_type="text/csv",
        filename=f"training_results_{task_uuid}.csv",
    )


@router.post("/validate/{task_id}", response_model=ModelValidateResponse)
async def validate_model(
    task_id: int,
    request: ModelValidateRequest = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """对已完成训练的模型执行验证集或测试集评估。"""
    _get_owned_task(db, task_id, current_user)
    request = request or ModelValidateRequest()
    result = training_service.validate_model(
        db=db,
        task_id=task_id,
        split=request.split,
        conf=request.conf,
        iou=request.iou,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info(
        "用户 %s 评估模型: task_id=%d, mAP50=%.4f",
        current_user.username,
        task_id,
        result.get("overall", {}).get("map50", 0),
    )
    return result


@router.post("/export/{task_id}", response_model=ModelExportResponse)
async def export_model(
    task_id: int,
    request: ModelExportRequest = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """导出训练权重、评估报告和模型版本信息。"""
    _get_owned_task(db, task_id, current_user)
    request = request or ModelExportRequest()
    result = training_service.export_model(
        db=db,
        task_id=task_id,
        version=request.version,
        description=request.description,
        set_default=request.set_default,
        upload_minio=request.upload_minio,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info(
        "用户 %s 导出模型: task_id=%d, version=%s",
        current_user.username,
        task_id,
        result["version"],
    )
    return result


@router.get("/download/{task_id}")
async def download_model(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """下载训练任务的 best.pt，缺失时回退到 last.pt。"""
    _get_owned_task(db, task_id, current_user)
    result = training_service.get_model_download_path(db, task_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return FileResponse(
        path=result["file_path"],
        media_type="application/octet-stream",
        filename=result["filename"],
    )


@router.post("/predict")
async def predict_test_image(
    file: UploadFile = File(..., description="测试图片"),
    task_id: int = Form(..., description="训练任务 ID"),
    conf: float = Form(0.25, ge=0, le=1, description="置信度阈值"),
    iou: float = Form(0.45, ge=0, le=1, description="NMS IoU 阈值"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """上传测试图片并使用已完成任务的模型进行预测。"""
    allowed_types = {"image/jpeg", "image/png", "image/bmp", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file.content_type}")

    task = _get_owned_task(db, task_id, current_user)
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="训练任务未完成，无法进行预测")

    weight_result = training_service.get_model_download_path(db, task_id)
    if "error" in weight_result:
        raise HTTPException(status_code=404, detail=weight_result["error"])

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传图片不能为空")

    suffix = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        from ultralytics import YOLO

        model = YOLO(weight_result["file_path"])
        results = model.predict(
            source=tmp_path,
            conf=conf,
            iou=iou,
            imgsz=task.img_size,
            device="cpu",
            save=False,
            verbose=False,
        )
        if not results:
            raise HTTPException(status_code=500, detail="模型未返回预测结果")

        result = results[0]
        detections = []
        if result.boxes is not None:
            for box in result.boxes:
                class_id = int(box.cls[0])
                if isinstance(model.names, dict):
                    class_name = model.names.get(class_id, f"class_{class_id}")
                else:
                    class_name = (
                        model.names[class_id]
                        if class_id < len(model.names)
                        else f"class_{class_id}"
                    )
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(
                    {
                        "class_name": class_name,
                        "class_id": class_id,
                        "confidence": round(float(box.conf[0]), 4),
                        "bbox": [
                            round(x1, 1),
                            round(y1, 1),
                            round(x2, 1),
                            round(y2, 1),
                        ],
                    }
                )

        encoded, buffer = cv2.imencode(
            ".jpg",
            result.plot(),
            [cv2.IMWRITE_JPEG_QUALITY, 85],
        )
        if not encoded:
            raise HTTPException(status_code=500, detail="标注图片编码失败")

        class_counts = {}
        for detection in detections:
            class_name = detection["class_name"]
            class_counts[class_name] = class_counts.get(class_name, 0) + 1

        return {
            "task_id": task_id,
            "task_uuid": task.task_uuid,
            "filename": file.filename,
            "total_objects": len(detections),
            "detections": detections,
            "class_counts": class_counts,
            "annotated_image": base64.b64encode(buffer).decode("utf-8"),
            "inference_time": round(float(result.speed.get("inference", 0)), 2),
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        await file.close()
