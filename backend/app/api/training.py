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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config.settings import settings
from app.core.logger import get_logger
from app.database.session import get_db
from app.entity.schemas import (
    TrainingTaskCreate,
    TrainingTaskResponse,
    TrainingMetricResponse,
)
from app.training.training_service import training_service

import os

logger = get_logger(__name__)

router = APIRouter(prefix="/api/training", tags=["模型训练"])


@router.get("/scenes")
async def list_training_scenes(
    db: Session = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    """获取可用于训练的启用场景。"""
    from app.entity.db_models import DetectionScene

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
    current_user=Depends(get_current_user),
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
    current_user=Depends(get_current_user),
):
    """获取当前用户的训练任务列表"""
    tasks = training_service.get_task_list(db, user_id=current_user.id)
    return {"total": len(tasks), "items": tasks}


@router.get("/status/{task_id}")
async def get_training_status(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取训练任务状态

    返回任务基本信息、当前进度和最新 epoch 指标
    前端可轮询此接口实现实时监控
    """
    status = training_service.get_training_status(db, task_id)
    if "error" in status:
        raise HTTPException(status_code=404, detail=status["error"])
    return status


@router.get("/metrics/{task_id}")
async def get_training_metrics(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取训练任务的所有 epoch 指标

    用于绘制完整的训练曲线（loss、mAP、precision、recall）
    """
    metrics = training_service.get_training_metrics(db, task_id)
    return {"task_id": task_id, "total": len(metrics), "metrics": metrics}


@router.post("/stop/{task_id}")
async def stop_training(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """停止正在运行的训练任务"""
    result = training_service.stop_training(db, task_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/results/{task_uuid}")
async def get_results_csv(
    task_uuid: str,
    current_user=Depends(get_current_user),
):
    """
    获取 Ultralytics 生成的原始 results.csv 文件

    可用于离线分析或导出到其他工具
    """
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
