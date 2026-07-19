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
  - POST   /api/training/validate/{task_id}          启动模型评估（异步）
  - GET    /api/training/validate/{task_id}/status   轮询评估状态与报告
"""

import asyncio
import base64
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.permissions import require_permission
from app.core.rbac import MODEL_MANAGE
from app.config.settings import settings
from app.core.logger import get_logger
from app.database.session import SessionLocal, get_db
from app.entity.db_models import DetectionScene, ModelEvaluation, TrainingTask
from app.entity.schemas import (
    ModelExportRequest,
    ModelExportResponse,
    ModelValidateRequest,
    ModelValidateStartResponse,
    ModelValidateStatusResponse,
    TrainingMetricResponse,
    TrainingTaskCreate,
    TrainingTaskResponse,
)
from app.training.training_service import (
    MODEL_READY_TASK_STATUSES,
    _task_output_dir,
    submit_model_task,
    training_service,
)
from app.services.model_management_service import model_management_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/training", tags=["模型训练"])

_backup_executor: ThreadPoolExecutor | None = None
_backup_executor_lock = threading.Lock()


def _get_backup_executor() -> ThreadPoolExecutor:
    """惰性创建可在应用重启后重新初始化的备份执行器。"""
    global _backup_executor
    with _backup_executor_lock:
        if _backup_executor is None:
            _backup_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="model-backup",
            )
        return _backup_executor


def _backup_exported_model(model_version_id: int) -> None:
    """响应返回后在后台备份正式模型，避免远程上传阻塞导出请求。"""
    started_at = time.perf_counter()
    logger.info("导出模型后台备份开始: model_version_id=%d", model_version_id)
    db = SessionLocal()
    try:
        model_management_service.backup_model(db, model_version_id)
        logger.info(
            "导出模型后台备份完成: model_version_id=%d, duration=%.1fs",
            model_version_id,
            time.perf_counter() - started_at,
        )
    except Exception as exc:
        db.rollback()
        logger.error(
            "导出模型后台备份失败: model_version_id=%d, duration=%.1fs, error=%s",
            model_version_id,
            time.perf_counter() - started_at,
            exc,
            exc_info=True,
        )
    finally:
        db.close()


def _schedule_export_backup(model_version_id: int) -> None:
    """把远程上传提交到专用线程池，避免占用 FastAPI 公共线程池。"""
    _get_backup_executor().submit(_backup_exported_model, model_version_id)


def shutdown_backup_executor() -> None:
    """应用退出时停止接收新的备份任务。"""
    global _backup_executor
    with _backup_executor_lock:
        executor = _backup_executor
        _backup_executor = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _export_model_in_worker(
    task_id: int,
    *,
    version: str | None,
    description: str | None,
    set_default: bool,
) -> dict:
    """在工作线程内使用独立数据库会话执行耗时导出。"""
    db = SessionLocal()
    try:
        return training_service.export_model(
            db=db,
            task_id=task_id,
            version=version,
            description=description,
            set_default=set_default,
            upload_minio=False,
        )
    finally:
        db.close()


async def _run_export_model(
    task_id: int,
    *,
    version: str | None,
    description: str | None,
    set_default: bool,
) -> dict:
    """隔离执行导出；测试或显式关闭隔离时回退到工作线程。"""
    kwargs = {
        "version": version,
        "description": description,
        "set_default": set_default,
    }
    if not settings.MODEL_TASK_PROCESS_ISOLATION:
        return await asyncio.to_thread(_export_model_in_worker, task_id, **kwargs)

    future = submit_model_task(_export_model_in_worker, task_id, **kwargs)
    return await asyncio.wrap_future(future)


def _get_owned_task(db: Session, task_id: int, _user) -> TrainingTask:
    """获取训练任务；调用方已通过模型管理权限校验。"""
    task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    return task


@router.get("/scenes")
def list_training_scenes(
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
def start_training(
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
def list_training_tasks(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """获取当前用户的训练任务列表"""
    tasks = training_service.get_task_list(db, user_id=None)
    return {"total": len(tasks), "items": tasks}


@router.post("/rescan")
def rescan_training_tasks(
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
def get_training_status(
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
def get_training_metrics(
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
def stop_training(
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
def get_results_csv(
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


@router.post("/validate/{task_id}", response_model=ModelValidateStartResponse)
def validate_model(
    task_id: int,
    request: ModelValidateRequest = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """启动模型评估（后台异步执行，前端轮询 /validate/{task_id}/status 获取结果）。"""
    _get_owned_task(db, task_id, current_user)
    request = request or ModelValidateRequest()
    result = training_service.start_validation(
        db=db,
        task_id=task_id,
        split=request.split,
        conf=request.conf,
        iou=request.iou,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info(
        "用户 %s 启动模型评估: task_id=%d, split=%s",
        current_user.username,
        task_id,
        request.split,
    )
    return result


@router.get("/validate/{task_id}/status", response_model=ModelValidateStatusResponse)
def get_validation_status(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """查询模型评估状态；completed 时携带评估报告。"""
    _get_owned_task(db, task_id, current_user)
    return training_service.get_validation_status(task_id, db=db)


@router.get("/validate/{task_id}/artifacts/{filename}")
def get_validation_artifact(
    task_id: int,
    filename: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """返回持久化评估记录关联的图表文件。"""
    task = _get_owned_task(db, task_id, current_user)
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="评估文件名无效")
    evaluations = (
        db.query(ModelEvaluation)
        .filter(ModelEvaluation.training_task_id == task_id)
        .order_by(ModelEvaluation.evaluated_at.desc(), ModelEvaluation.id.desc())
        .all()
    )
    artifact_path = next(
        (
            Path(item.artifact_paths[safe_name])
            for item in evaluations
            if item.artifact_paths and safe_name in item.artifact_paths
        ),
        None,
    )
    if artifact_path is None or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="评估图表不存在")
    try:
        artifact_path.resolve().relative_to(_task_output_dir(task.task_uuid).resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="评估图表路径无效")
    return FileResponse(path=str(artifact_path), filename=safe_name)


@router.post("/export/{task_id}", response_model=ModelExportResponse)
async def export_model(
    task_id: int,
    background_tasks: BackgroundTasks,
    request: ModelExportRequest = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(MODEL_MANAGE)),
):
    """导出训练权重、评估报告和模型版本信息。"""
    _get_owned_task(db, task_id, current_user)
    request = request or ModelExportRequest()
    result = await _run_export_model(
        task_id,
        version=request.version,
        description=request.description,
        set_default=request.set_default,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info(
        "用户 %s 导出模型: task_id=%d, version=%s",
        current_user.username,
        task_id,
        result["version"],
    )
    if request.upload_minio:
        background_tasks.add_task(
            _schedule_export_backup,
            result["model_version_id"],
        )
        result["message"] = f"模型已导出为版本 {result['version']}，备份将在后台完成"
    return result


@router.get("/download/{task_id}")
def download_model(
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
    """上传测试图片并使用训练任务产出的模型进行预测。"""
    allowed_types = {"image/jpeg", "image/png", "image/bmp", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file.content_type}")

    task = _get_owned_task(db, task_id, current_user)
    if task.status not in MODEL_READY_TASK_STATUSES:
        raise HTTPException(status_code=400, detail="训练任务当前状态无法进行模型预测")

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
