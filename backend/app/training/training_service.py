"""
模型训练服务

职责：
  - 封装 YOLOv11 训练启动、监控、停止逻辑
  - 支持本地 CPU 训练和 GPU 训练
  - 训练在后台线程中执行，不阻塞 API 请求
  - 实时解析训练指标并写入数据库
  - 解析 Ultralytics 生成的 results.csv 获取训练日志

使用方式：
  from app.training.training_service import training_service

  # 启动训练
  task = training_service.start_training(
      db=db,
      user_id=current_user.id,
      scene_id=scene.id,
      config={"model_name": "yolov11n", "epochs": 50, "batch_size": 8}
  )

  # 查询训练状态
  status = training_service.get_training_status(db, task_id)

  # 获取训练指标
  metrics = training_service.get_training_metrics(db, task_id)
"""

import csv
import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.config.settings import settings
from app.core.logger import get_logger
from app.database.session import SessionLocal
from app.entity.db_models import (
    DetectionScene,
    ModelVersion,
    TrainingMetric,
    TrainingTask,
)

logger = get_logger(__name__)

# ── 训练进程注册表 ────────────────────────────────────
# 存储正在运行的训练任务的 model 引用，用于中途停止训练
# key: task_uuid, value: YOLO model 实例
_running_tasks: dict = {}
_running_lock = threading.Lock()


class TrainingService:
    """模型训练服务 — 封装 YOLOv11 训练全流程"""

    @staticmethod
    def start_training(
        db,
        user_id: int,
        scene_id: int,
        config: dict,
    ) -> TrainingTask:
        """
        创建并启动训练任务

        流程：
          1. 在数据库中创建 TrainingTask 记录（状态 pending）
          2. 启动后台守护线程执行 _run_training()
          3. 立即返回任务对象（前端通过轮询获取进度）

        Args:
            db: SQLAlchemy 数据库会话
            user_id: 操作用户 ID
            scene_id: 关联的检测场景 ID
            config: 训练配置字典，支持的字段：
                - model_name: 基础模型名称（yolov11n/s/m/l/x）
                - epochs: 训练轮数
                - img_size: 图像尺寸
                - batch_size: 批次大小
                - device: 训练设备（cpu / 0 / 1）
                - optimizer: 优化器（SGD / Adam / AdamW）
                - lr0: 初始学习率
                - augment_config: 数据增强配置
                - dataset_path: 数据集路径（可选，默认使用场景目录）
                - data_yaml: data.yaml 路径（可选，自动查找）

        Returns:
            创建的 TrainingTask 数据库对象
        """
        # ── 生成唯一任务标识 ──
        task_uuid = str(uuid.uuid4())[:8]

        # ── 查找 data.yaml ──
        data_yaml = config.get("data_yaml")
        dataset_path = config.get("dataset_path", "")
        if not data_yaml and dataset_path:
            # 在数据集目录下查找 data.yaml
            yaml_candidate = os.path.join(dataset_path, "data.yaml")
            if os.path.exists(yaml_candidate):
                data_yaml = yaml_candidate

        # ── 创建数据库记录 ──
        task = TrainingTask(
            user_id=user_id,
            scene_id=scene_id,
            task_uuid=task_uuid,
            status="pending",
            model_name=config.get("model_name", "yolo11n"),
            epochs=config.get("epochs", 50),
            img_size=config.get("img_size", 640),
            batch_size=config.get("batch_size", 8),
            device=config.get("device", "cpu"),
            optimizer=config.get("optimizer", "SGD"),
            lr0=config.get("lr0", 0.01),
            augment_config=config.get("augment_config"),
            dataset_path=dataset_path,
            data_yaml=data_yaml,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        # ── 启动后台训练线程 ──
        thread = threading.Thread(
            target=TrainingService._run_training,
            args=(task.id, task.task_uuid, config),
            daemon=True,  # 守护线程：主进程退出时自动结束
            name=f"train-{task_uuid}",
        )
        thread.start()

        logger.info(
            "训练任务已启动：task_id=%d, uuid=%s, model=%s, epochs=%d",
            task.id,
            task_uuid,
            task.model_name,
            task.epochs,
        )
        return task

    @staticmethod
    def _run_training(task_id: int, task_uuid: str, config: dict):
        """
        在后台线程中执行 YOLOv11 训练（内部方法）

        流程：
          1. 更新任务状态为 running
          2. 加载预训练模型
          3. 调用 model.train() 开始训练
          4. 训练完成后解析结果，更新状态为 completed
          5. 异常时更新状态为 failed

        Args:
            task_id: 训练任务数据库 ID
            task_uuid: 任务唯一标识
            config: 训练配置字典
        """
        # ── 创建独立的数据库会话（后台线程不能复用请求级会话）──
        db = SessionLocal()
        try:
            task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
            if not task:
                logger.error("训练任务不存在：task_id=%d", task_id)
                return

            # ── 更新状态为 running ──
            task.status = "running"
            task.started_at = datetime.now()
            db.commit()

            # ── 导入 ultralytics ──
            from ultralytics import YOLO

            # ── 加载预训练模型 ──
            model_name = config.get("model_name", "yolo11n")
            logger.info("加载预训练模型：%s（首次使用将自动下载）", model_name)
            model = YOLO(model_name)

            # ── 注册到运行中任务表（用于中途停止）──
            with _running_lock:
                _running_tasks[task_uuid] = model

            # ── 确定 data.yaml 路径 ──
            data_yaml = config.get("data_yaml", "")
            if not data_yaml:
                dataset_path = config.get("dataset_path", "")
                data_yaml = os.path.join(dataset_path, "data.yaml")

            if not os.path.exists(data_yaml):
                raise FileNotFoundError(f"data.yaml 不存在：{data_yaml}")

            data_yaml_dir = os.path.dirname(data_yaml)
            original_cwd = os.getcwd()

            # 临时修改 data.yaml 的 path 为绝对路径（训练后恢复）
            with open(data_yaml, "r", encoding="utf-8") as f:
                original_content = f.read()
            
            modified_content = original_content.replace(
                "path: .",
                f"path: {data_yaml_dir}"
            )
            with open(data_yaml, "w", encoding="utf-8") as f:
                f.write(modified_content)
            logger.info(f"临时修改 data.yaml path 为绝对路径：{data_yaml_dir}")

            train_kwargs = {
                "data": data_yaml,
                "epochs": config.get("epochs", 50),
                "imgsz": config.get("img_size", 640),
                "batch": config.get("batch_size", 8),
                "device": config.get("device", "cpu"),
                "optimizer": config.get("optimizer", "SGD"),
                "lr0": config.get("lr0", 0.01),
                "project": os.path.join(original_cwd, settings.TRAIN_OUTPUT_DIR),
                "name": f"task_{task_uuid}",
                "exist_ok": True,
                "verbose": True,
                "save": True,
                "plots": False,
            }
            train_kwargs.update(_get_augmentation_kwargs(config.get("augment_config")))

            # ── 注册训练回调：每个 epoch 结束时更新数据库 ──
            def on_train_epoch_end(trainer):
                """训练 epoch 结束时的回调"""
                try:
                    # 从 trainer 获取当前 epoch 指标
                    epoch = trainer.epoch + 1  # ultralytics epoch 从 0 开始
                    metrics = trainer.metrics or {}
                    loss_metrics = _get_epoch_loss_metrics(trainer)

                    metric_record = TrainingMetric(
                        task_id=task_id,
                        epoch=epoch,
                        box_loss=_safe_float(loss_metrics.get("train/box_loss")),
                        cls_loss=_safe_float(loss_metrics.get("train/cls_loss")),
                        dfl_loss=_safe_float(loss_metrics.get("train/dfl_loss")),
                        precision=float(
                            metrics.get("metrics/precision(B)", 0)
                            if isinstance(metrics, dict)
                            else 0
                        ),
                        recall=float(
                            metrics.get("metrics/recall(B)", 0)
                            if isinstance(metrics, dict)
                            else 0
                        ),
                        map50=float(
                            metrics.get("metrics/mAP50(B)", 0)
                            if isinstance(metrics, dict)
                            else 0
                        ),
                        map50_95=float(
                            metrics.get("metrics/mAP50-95(B)", 0)
                            if isinstance(metrics, dict)
                            else 0
                        ),
                    )
                    db.add(metric_record)

                    # 更新任务进度
                    total_epochs = config.get("epochs", 50)
                    task.current_epoch = epoch
                    task.progress = int((epoch / total_epochs) * 100)
                    db.commit()

                    logger.debug(
                        "训练进度：task=%s epoch=%d/%d box_loss=%.4f",
                        task_uuid,
                        epoch,
                        total_epochs,
                        metric_record.box_loss or 0,
                    )
                except Exception as e:
                    logger.warning("训练回调异常（不影响训练）：%s", str(e))
                    db.rollback()

            # 添加回调
            model.add_callback("on_train_epoch_end", on_train_epoch_end)

            # ── 开始训练（阻塞直到完成）──
            logger.info(
                "开始训练：data=%s, epochs=%d", data_yaml, train_kwargs["epochs"]
            )
            results = model.train(**train_kwargs)

            # ── 训练完成，解析最终结果 ──
            task.status = "completed"
            task.progress = 100
            task.current_epoch = config.get("epochs", 50)
            task.completed_at = datetime.now()
            db.commit()

            # ── 从 results.csv 补充最终指标 ──
            project_path = os.path.join(original_cwd, settings.TRAIN_OUTPUT_DIR)
            TrainingService._parse_final_results(db, task_id, task_uuid, config, project_path)

            logger.info("训练完成：task_id=%d, uuid=%s", task_id, task_uuid)

        except FileNotFoundError as e:
            logger.error("训练文件缺失：task_id=%d, error=%s", task_id, str(e))
            task.status = "failed"
            task.error_message = str(e)
            db.commit()

        except Exception as e:
            logger.error(
                "训练异常：task_id=%d, error=%s", task_id, str(e), exc_info=True
            )
            task.status = "failed"
            task.error_message = str(e)[:2000]  # 限制错误信息长度
            db.commit()

        finally:
            # 恢复 data.yaml 原始内容
            try:
                with open(data_yaml, "w", encoding="utf-8") as f:
                    f.write(original_content)
                logger.info(f"恢复 data.yaml 原始内容")
            except Exception:
                pass

            # 恢复工作目录
            try:
                os.chdir(original_cwd)
                logger.info(f"恢复工作目录：{original_cwd}")
            except Exception:
                pass

            with _running_lock:
                _running_tasks.pop(task_uuid, None)
            db.close()

    @staticmethod
    def _parse_final_results(db, task_id: int, task_uuid: str, config: dict, project_path: str = None):
        """
        训练完成后从 results.csv 解析最终指标并补充到数据库

        Ultralytics 在训练过程中会将每个 epoch 的指标写入 results.csv，
        回调中可能遗漏最后几个 epoch，此方法确保数据完整。

        Args:
            db: 数据库会话
            task_id: 训练任务 ID
            task_uuid: 任务 UUID
            config: 训练配置
            project_path: 训练输出目录（绝对路径）
        """
        if project_path is None:
            project_path = settings.TRAIN_OUTPUT_DIR

        results_csv = os.path.join(
            project_path,
            f"task_{task_uuid}",
            "results.csv",
        )

        if not os.path.exists(results_csv):
            logger.warning("results.csv 不存在：%s", results_csv)
            return

        try:
            # 读取已有的 epoch 记录
            existing_metrics = {
                metric.epoch: metric
                for metric in db.query(TrainingMetric).filter(TrainingMetric.task_id == task_id).all()
            }

            # 解析 results.csv
            with open(results_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # results.csv 的列名可能带空格
                    row = {k.strip(): v.strip() for k, v in row.items()}
                    epoch = int(row.get("epoch", 0))  # Ultralytics CSV 中 epoch 从 1 开始
                    metric_values = {
                        "box_loss": _safe_float(row.get("train/box_loss", "")),
                        "cls_loss": _safe_float(row.get("train/cls_loss", "")),
                        "dfl_loss": _safe_float(row.get("train/dfl_loss", "")),
                        "precision": _safe_float(row.get("metrics/precision(B)", "")),
                        "recall": _safe_float(row.get("metrics/recall(B)", "")),
                        "map50": _safe_float(row.get("metrics/mAP50(B)", "")),
                        "map50_95": _safe_float(row.get("metrics/mAP50-95(B)", "")),
                        "lr": _safe_float(row.get("lr/pg0", "")),
                    }

                    if metric := existing_metrics.get(epoch):
                        for field, value in metric_values.items():
                            setattr(metric, field, value)
                    else:
                        db.add(TrainingMetric(task_id=task_id, epoch=epoch, **metric_values))

            db.commit()
            logger.info("results.csv 解析完成，指标已补充到数据库")

        except Exception as e:
            logger.warning("results.csv 解析异常（不影响训练结果）：%s", str(e))
            db.rollback()

    @staticmethod
    def get_training_status(db, task_id: int) -> dict:
        """
        获取训练任务状态

        返回任务基本信息 + 当前进度 + 最新指标

        Args:
            db: 数据库会话
            task_id: 训练任务 ID

        Returns:
            状态字典，包含：
                - task: 任务基本信息
                - latest_metric: 最新 epoch 的指标
                - is_running: 是否在运行中
        """
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}

        # 获取最新一条指标记录
        latest_metric = (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task_id)
            .order_by(TrainingMetric.epoch.desc())
            .first()
        )

        # 检查是否在运行中
        with _running_lock:
            is_running = task.task_uuid in _running_tasks

        return {
            "task": {
                "id": task.id,
                "task_uuid": task.task_uuid,
                "status": task.status,
                "model_name": task.model_name,
                "epochs": task.epochs,
                "current_epoch": task.current_epoch,
                "progress": task.progress,
                "device": task.device,
                "batch_size": task.batch_size,
                "img_size": task.img_size,
                "started_at": str(task.started_at) if task.started_at else None,
                "completed_at": str(task.completed_at) if task.completed_at else None,
                "error_message": task.error_message,
            },
            "latest_metric": {
                "epoch": latest_metric.epoch,
                "box_loss": latest_metric.box_loss,
                "cls_loss": latest_metric.cls_loss,
                "dfl_loss": latest_metric.dfl_loss,
                "precision": latest_metric.precision,
                "recall": latest_metric.recall,
                "map50": latest_metric.map50,
                "map50_95": latest_metric.map50_95,
                "lr": latest_metric.lr,
            }
            if latest_metric
            else None,
            "is_running": is_running,
        }

    @staticmethod
    def get_training_metrics(db, task_id: int) -> list:
        """
        获取训练任务的所有 epoch 指标（用于绘制训练曲线）

        Args:
            db: 数据库会话
            task_id: 训练任务 ID

        Returns:
            指标列表，每项包含 epoch 和各项指标值
        """
        metrics = (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task_id)
            .order_by(TrainingMetric.epoch.asc())
            .all()
        )

        return [
            {
                "epoch": m.epoch,
                "box_loss": m.box_loss,
                "cls_loss": m.cls_loss,
                "dfl_loss": m.dfl_loss,
                "precision": m.precision,
                "recall": m.recall,
                "map50": m.map50,
                "map50_95": m.map50_95,
                "lr": m.lr,
            }
            for m in metrics
        ]

    @staticmethod
    def stop_training(db, task_id: int) -> dict:
        """
        停止正在运行的训练任务

        通过 ultralytics 的 model.train() 中断机制停止训练

        Args:
            db: 数据库会话
            task_id: 训练任务 ID

        Returns:
            操作结果字典
        """
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}

        if task.status != "running":
            return {"error": f"任务当前状态为 {task.status}，无法停止"}

        with _running_lock:
            model = _running_tasks.get(task.task_uuid)
            if model:
                # ultralytics 支持通过设置 model.train 的 interrupt 来停止
                try:
                    model.trainer.stop()
                except Exception as e:
                    logger.warning("停止训练异常：%s", str(e))

        # 更新状态
        task.status = "cancelled"
        task.completed_at = datetime.now()
        db.commit()

        logger.info("训练任务已停止：task_id=%d", task_id)
        return {"message": "训练任务已停止", "task_id": task_id}

    @staticmethod
    def get_task_list(db, user_id: int = None, limit: int = 20) -> list:
        """
        获取训练任务列表

        Args:
            db: 数据库会话
            user_id: 用户 ID（None 则返回所有用户的任务）
            limit: 返回数量限制

        Returns:
            任务列表
        """
        query = db.query(TrainingTask)
        if user_id:
            query = query.filter(TrainingTask.user_id == user_id)

        tasks = query.order_by(TrainingTask.created_at.desc()).limit(limit).all()

        return [
            {
                "id": t.id,
                "task_uuid": t.task_uuid,
                "status": t.status,
                "model_name": t.model_name,
                "epochs": t.epochs,
                "current_epoch": t.current_epoch,
                "progress": t.progress,
                "device": t.device,
                "created_at": str(t.created_at),
                "started_at": str(t.started_at) if t.started_at else None,
                "completed_at": str(t.completed_at) if t.completed_at else None,
            }
            for t in tasks
        ]

    @staticmethod
    def validate_model(
        db,
        task_id: int,
        split: str = "val",
        conf: float = 0.001,
        iou: float = 0.6,
    ) -> dict:
        """在指定数据集划分上评估已完成训练的最佳权重。"""
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}
        if task.status != "completed":
            return {"error": f"训练任务状态为 {task.status}，只有已完成的任务才能评估"}

        weights_path = _task_output_dir(task.task_uuid) / "weights" / "best.pt"
        if not weights_path.exists():
            return {"error": f"模型权重不存在: {weights_path}"}

        data_yaml = _resolve_data_yaml(task)
        if not data_yaml:
            return {"error": "data.yaml 不存在"}

        scene = (
            db.query(DetectionScene)
            .filter(DetectionScene.id == task.scene_id)
            .first()
        )
        if not scene:
            return {"error": "关联场景不存在"}

        logger.info(
            "开始模型评估: task_id=%d, weights=%s, split=%s",
            task_id,
            weights_path,
            split,
        )

        try:
            from ultralytics import YOLO

            model = YOLO(str(weights_path))
            task_output_dir = _task_output_dir(task.task_uuid)
            results = model.val(
                data=str(data_yaml),
                split=split,
                conf=conf,
                iou=iou,
                imgsz=task.img_size,
                device="cpu",
                save_json=True,
                plots=True,
                project=str(task_output_dir.parent),
                name=task_output_dir.name,
                exist_ok=True,
                verbose=False,
            )
            overall, per_class = _parse_evaluation_results(results, model.names)

            model_version = (
                db.query(ModelVersion)
                .filter(ModelVersion.training_task_id == task_id)
                .first()
            )
            if not model_version:
                existing_count = (
                    db.query(ModelVersion)
                    .filter(ModelVersion.scene_id == task.scene_id)
                    .count()
                )
                version = f"v{existing_count + 1}.0.0"
                model_version = ModelVersion(
                    scene_id=task.scene_id,
                    training_task_id=task_id,
                    version=version,
                    model_name=f"{task.model_name}_{scene.name}_{version}",
                    model_type=task.model_name,
                    model_path=str(weights_path),
                    description=f"训练任务 {task.task_uuid} 自动产出",
                )
                db.add(model_version)

            model_version.map50 = overall["map50"]
            model_version.map50_95 = overall["map50_95"]
            model_version.precision = overall["precision"]
            model_version.recall = overall["recall"]
            model_version.per_class_ap = per_class
            model_version.file_size = weights_path.stat().st_size
            db.commit()
            db.refresh(model_version)

            report = {
                "task_id": task_id,
                "task_uuid": task.task_uuid,
                "split": split,
                "overall": overall,
                "per_class": per_class,
                "model_version_id": model_version.id,
                "model_version": model_version.version,
            }
            logger.info(
                "模型评估完成: task_id=%d, mAP50=%.4f, mAP50-95=%.4f",
                task_id,
                overall["map50"],
                overall["map50_95"],
            )
            return report
        except Exception as e:
            db.rollback()
            logger.error(
                "模型评估异常: task_id=%d, error=%s",
                task_id,
                str(e),
                exc_info=True,
            )
            return {"error": f"评估失败: {str(e)}"}

    @staticmethod
    def export_model(
        db,
        task_id: int,
        version: str = None,
        description: str = None,
        set_default: bool = False,
        upload_minio: bool = True,
    ) -> dict:
        """将训练权重、评估报告和图表导出为正式模型版本。"""
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}
        if task.status != "completed":
            return {"error": f"训练任务状态为 {task.status}，只有已完成的任务才能导出"}

        weights_path = _task_output_dir(task.task_uuid) / "weights" / "best.pt"
        if not weights_path.exists():
            return {"error": f"模型权重不存在: {weights_path}"}

        scene = (
            db.query(DetectionScene)
            .filter(DetectionScene.id == task.scene_id)
            .first()
        )
        if not scene:
            return {"error": "关联场景不存在"}

        model_version = (
            db.query(ModelVersion)
            .filter(ModelVersion.training_task_id == task_id)
            .first()
        )
        if not version:
            if model_version:
                version = model_version.version
            else:
                existing_count = (
                    db.query(ModelVersion)
                    .filter(ModelVersion.scene_id == task.scene_id)
                    .count()
                )
                version = f"v{existing_count + 1}.0.0"

        evaluation = TrainingService.validate_model(db, task_id, split="val")
        if "error" in evaluation:
            return {"error": evaluation["error"]}

        try:
            export_dir = _backend_dir() / "models" / f"{scene.name}_{version}"
            export_dir.mkdir(parents=True, exist_ok=True)
            exported_weight = export_dir / "best.pt"
            shutil.copy2(weights_path, exported_weight)

            task_output_dir = _task_output_dir(task.task_uuid)
            for plot_name in (
                "confusion_matrix.png",
                "PR_curve.png",
                "F1_curve.png",
                "results.png",
            ):
                plot_path = task_output_dir / plot_name
                if plot_path.exists():
                    shutil.copy2(plot_path, export_dir / plot_name)

            overall = evaluation["overall"]
            per_class = evaluation["per_class"]
            report = {
                "version": version,
                "model_name": task.model_name,
                "scene": scene.name,
                "training_task": task.task_uuid,
                "evaluation": {
                    "split": "val",
                    "overall": overall,
                    "per_class": per_class,
                },
                "training_config": {
                    "epochs": task.epochs,
                    "batch_size": task.batch_size,
                    "img_size": task.img_size,
                    "optimizer": task.optimizer,
                    "lr0": task.lr0,
                    "device": task.device,
                    "augment_config": task.augment_config,
                },
                "exported_at": datetime.now().isoformat(),
            }
            with (export_dir / "eval_report.json").open("w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            minio_url = None
            if upload_minio:
                try:
                    from app.storage.minio_client import MinIOClient

                    object_name = f"models/{scene.name}/{version}/best.pt"
                    minio_url = MinIOClient().upload_file(
                        object_name,
                        str(exported_weight),
                    )
                except Exception as e:
                    logger.warning("MinIO 上传失败（不影响导出）: %s", str(e))

            model_version = (
                db.query(ModelVersion)
                .filter(ModelVersion.training_task_id == task_id)
                .first()
            )
            model_version.version = version
            model_version.model_name = f"{task.model_name}_{scene.name}_{version}"
            model_version.model_path = str(exported_weight)
            model_version.minio_url = minio_url
            model_version.map50 = overall["map50"]
            model_version.map50_95 = overall["map50_95"]
            model_version.precision = overall["precision"]
            model_version.recall = overall["recall"]
            model_version.per_class_ap = per_class
            model_version.file_size = exported_weight.stat().st_size
            model_version.description = description or f"训练任务 {task.task_uuid} 导出"

            if set_default:
                db.query(ModelVersion).filter(
                    ModelVersion.scene_id == task.scene_id,
                    ModelVersion.id != model_version.id,
                ).update({"is_default": False}, synchronize_session=False)
                model_version.is_default = True

            db.commit()
            db.refresh(model_version)
            return {
                "model_version_id": model_version.id,
                "version": version,
                "model_name": model_version.model_name,
                "model_path": str(exported_weight),
                "export_dir": str(export_dir),
                "minio_url": minio_url,
                "file_size": model_version.file_size,
                "evaluation": {
                    **overall,
                    "per_class": per_class,
                },
                "is_default": model_version.is_default,
                "message": f"模型已导出为版本 {version}",
            }
        except Exception as e:
            db.rollback()
            logger.error(
                "模型导出异常: task_id=%d, error=%s",
                task_id,
                str(e),
                exc_info=True,
            )
            return {"error": f"导出失败: {str(e)}"}

    @staticmethod
    def get_model_download_path(db, task_id: int) -> dict:
        """返回训练任务可下载的最佳权重，必要时回退到 last.pt。"""
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}

        weights_dir = _task_output_dir(task.task_uuid) / "weights"
        for weight_name in ("best.pt", "last.pt"):
            weight_path = weights_dir / weight_name
            if weight_path.exists():
                return {
                    "file_path": str(weight_path),
                    "filename": f"{weight_path.stem}_{task.task_uuid}.pt",
                    "file_size": weight_path.stat().st_size,
                }
        return {"error": "模型权重文件不存在"}

    @staticmethod
    def parse_results_csv(results_csv_path: str) -> list:
        """
        独立解析 results.csv 文件（工具方法，可用于离线分析）

        Args:
            results_csv_path: results.csv 文件路径

        Returns:
            解析后的指标列表
        """
        metrics = []
        if not os.path.exists(results_csv_path):
            return metrics

        with open(results_csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row = {k.strip(): v.strip() for k, v in row.items()}
                metrics.append(
                    {
                        "epoch": int(row.get("epoch", 0)),
                        "box_loss": _safe_float(row.get("train/box_loss", "")),
                        "cls_loss": _safe_float(row.get("train/cls_loss", "")),
                        "dfl_loss": _safe_float(row.get("train/dfl_loss", "")),
                        "precision": _safe_float(row.get("metrics/precision(B)", "")),
                        "recall": _safe_float(row.get("metrics/recall(B)", "")),
                        "map50": _safe_float(row.get("metrics/mAP50(B)", "")),
                        "map50_95": _safe_float(row.get("metrics/mAP50-95(B)", "")),
                        "lr": _safe_float(row.get("lr/pg0", "")),
                    }
                )
        return metrics


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════


# Ultralytics 数据增强参数白名单，防止 augment_config 覆盖 data/project 等核心参数。
_AUGMENTATION_KEYS = frozenset(
    {
        "hsv_h",
        "hsv_s",
        "hsv_v",
        "degrees",
        "translate",
        "scale",
        "shear",
        "perspective",
        "flipud",
        "fliplr",
        "bgr",
        "mosaic",
        "mixup",
        "cutmix",
        "copy_paste",
        "erasing",
    }
)


def _backend_dir() -> Path:
    """返回 backend 根目录。"""
    return Path(__file__).resolve().parents[2]


def _training_output_dir() -> Path:
    """返回训练产物根目录，兼容绝对路径配置。"""
    output_dir = Path(settings.TRAIN_OUTPUT_DIR)
    if not output_dir.is_absolute():
        output_dir = _backend_dir() / output_dir
    return output_dir.resolve()


def _task_output_dir(task_uuid: str) -> Path:
    """返回指定训练任务的产物目录。"""
    return _training_output_dir() / f"task_{task_uuid}"


def _resolve_data_yaml(task: TrainingTask):
    """从任务记录中解析存在的 data.yaml 路径。"""
    candidates = []
    if task.data_yaml:
        candidates.append(Path(task.data_yaml))
    if task.dataset_path:
        candidates.append(Path(task.dataset_path) / "data.yaml")

    for candidate in candidates:
        paths = [candidate]
        if not candidate.is_absolute():
            paths.append(_backend_dir() / candidate)
        for path in paths:
            if path.exists():
                return path.resolve()
    return None


def _get_augmentation_kwargs(augment_config) -> dict:
    """过滤并返回 Ultralytics 支持的数据增强参数。"""
    if not isinstance(augment_config, dict):
        return {}
    return {
        key: value
        for key, value in augment_config.items()
        if key in _AUGMENTATION_KEYS and value is not None
    }


def _class_name(class_names, class_id: int) -> str:
    if isinstance(class_names, dict):
        return class_names.get(class_id, f"class_{class_id}")
    if class_id < len(class_names):
        return class_names[class_id]
    return f"class_{class_id}"


def _parse_evaluation_results(results, class_names) -> tuple[dict, dict]:
    """将 Ultralytics 验证结果转换为可序列化指标。"""
    box = getattr(results, "box", None)
    if box is None:
        raise ValueError("评估结果中缺少目标检测指标")

    overall = {
        "precision": float(box.mp),
        "recall": float(box.mr),
        "map50": float(box.map50),
        "map50_95": float(box.map),
    }
    per_class = {}
    ap_values = getattr(box, "ap", None)
    ap50_values = getattr(box, "ap50", None)
    if ap_values is None or ap50_values is None:
        return overall, per_class

    instance_values = getattr(box, "nt_per_class", None)
    if instance_values is None:
        instance_values = getattr(box, "np", None)

    for class_id, ap50 in enumerate(ap50_values):
        metrics = {
            "ap50": round(float(ap50), 4),
            "ap50_95": round(float(ap_values[class_id]), 4),
        }
        if instance_values is not None and class_id < len(instance_values):
            metrics["instances"] = int(instance_values[class_id])
        per_class[_class_name(class_names, class_id)] = metrics
    return overall, per_class


def _get_epoch_loss_metrics(trainer) -> dict:
    """获取当前 epoch 的平均训练损失。"""
    tloss = getattr(trainer, "tloss", None)
    if tloss is None or not hasattr(trainer, "label_loss_items"):
        return {}
    return trainer.label_loss_items(tloss)


def _safe_float(value) -> float:
    """安全地将字符串转换为浮点数，失败时返回 None"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ══════════════════════════════════════════════════════════════
# 全局单例
# ══════════════════════════════════════════════════════════════

training_service = TrainingService()