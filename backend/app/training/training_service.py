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
import hashlib
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
    ModelEvaluation,
    ModelVersion,
    TrainingMetric,
    TrainingTask,
    User,
)

logger = get_logger(__name__)

# ── 训练进程注册表 ────────────────────────────────────
# 存储正在运行的训练任务的 model 引用，用于中途停止训练
# key: task_uuid, value: YOLO model 实例
_running_tasks: dict = {}
_running_lock = threading.Lock()

# ── 评估任务注册表 ────────────────────────────────────
# 后台评估的状态与最近一次报告（进程内存，重启后清空，前端轮询读取）
# key: task_id, value: {status, split, report, error, started_at, completed_at, thread}
_running_evaluations: dict = {}
_eval_lock = threading.Lock()


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

        # ── 落盘初始元数据（产物自描述，用于数据库重建后恢复）──
        _write_task_meta(task)

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
            _write_task_meta(task)

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

            # ── 开始训练（阻塞直到完成或被停止）──
            logger.info(
                "开始训练：data=%s, epochs=%d", data_yaml, train_kwargs["epochs"]
            )
            results = model.train(**train_kwargs)

            # ── 判断是正常完成还是被用户中途停止 ──
            # 注意：不能用 trainer.stop 判断——Ultralytics 正常训练到最后一个
            # epoch 时也会把 trainer.stop 置为 True（final_epoch）。只有
            # stop_training() 会把任务状态写成 "stopping"，这是唯一可靠的信号。
            db.refresh(task)
            if task.status == "stopping":
                task.status = "cancelled"
                task.completed_at = datetime.now()
                db.commit()
                _write_task_meta(task)
                logger.info("训练已被停止：task_id=%d, uuid=%s", task_id, task_uuid)
                return

            # ── 训练正常完成，解析最终结果 ──
            task.status = "completed"
            task.progress = 100
            task.current_epoch = config.get("epochs", 50)
            task.completed_at = datetime.now()
            db.commit()

            # ── 从 results.csv 补充最终指标 ──
            project_path = os.path.join(original_cwd, settings.TRAIN_OUTPUT_DIR)
            TrainingService._parse_final_results(db, task_id, task_uuid, config, project_path)

            # ── 落盘最终元数据（含 completed 状态）──
            db.refresh(task)
            _write_task_meta(task)

            logger.info("训练完成：task_id=%d, uuid=%s", task_id, task_uuid)

        except FileNotFoundError as e:
            logger.error("训练文件缺失：task_id=%d, error=%s", task_id, str(e))
            task.status = "failed"
            task.error_message = str(e)
            db.commit()
            _write_task_meta(task)

        except Exception as e:
            logger.error(
                "训练异常：task_id=%d, error=%s", task_id, str(e), exc_info=True
            )
            task.status = "failed"
            task.error_message = str(e)[:2000]  # 限制错误信息长度
            db.commit()
            _write_task_meta(task)

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

        stop_flag_set = False
        with _running_lock:
            model = _running_tasks.get(task.task_uuid)
            if model is not None:
                # Ultralytics 的 BaseTrainer.stop 是布尔标志（不是方法）：
                # 训练循环在每个 epoch 末尾检查 `if self.stop: break`，
                # 因此这里只需把标志置为 True，训练会在当前 epoch 结束后优雅退出。
                try:
                    trainer = getattr(model, "trainer", None)
                    if trainer is not None:
                        trainer.stop = True
                        stop_flag_set = True
                    else:
                        # trainer 尚未创建（训练还在预热/加载阶段），
                        # 记录待停止标记，交由后台线程在启动后自行退出。
                        logger.warning(
                            "停止训练：trainer 尚未就绪，task_uuid=%s", task.task_uuid
                        )
                except Exception as e:
                    logger.warning("停止训练异常：%s", str(e))
            else:
                # 不在运行注册表中：线程可能已结束或从未真正启动。
                logger.warning(
                    "停止训练：运行注册表中无此任务，task_uuid=%s", task.task_uuid
                )

        # 更新状态为 stopping：真正的 cancelled 状态由后台线程在训练循环
        # 实际退出后写入，避免“页面显示已停止但后端仍在训练”的假象。
        task.status = "stopping" if stop_flag_set else "cancelled"
        if not stop_flag_set:
            task.completed_at = datetime.now()
        db.commit()
        _write_task_meta(task)

        logger.info(
            "训练停止请求已提交：task_id=%d, flag_set=%s", task_id, stop_flag_set
        )
        return {
            "message": "已请求停止训练，将在当前 epoch 结束后退出"
            if stop_flag_set
            else "训练任务已停止",
            "task_id": task_id,
            "status": task.status,
        }

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

        cached_evaluation, signature = _find_cached_evaluation(
            db,
            task,
            split=split,
            conf=conf,
            iou=iou,
        )
        if cached_evaluation is not None:
            logger.info(
                "复用模型评估缓存: task_id=%d, evaluation_id=%d",
                task_id,
                cached_evaluation.id,
            )
            return _evaluation_report(cached_evaluation, task)
        if signature is None:
            weights_path = _task_output_dir(task.task_uuid) / "weights" / "best.pt"
            if not weights_path.exists():
                return {"error": f"模型权重不存在: {weights_path}"}
            return {"error": "data.yaml 不存在"}

        weights_path = signature["weights_path"]
        data_yaml = signature["data_yaml"]

        scene = (
            db.query(DetectionScene)
            .filter(DetectionScene.id == task.scene_id)
            .first()
        )
        if not scene:
            return {"error": "关联场景不存在"}

        eval_device = _resolve_eval_device(task.device)
        logger.info(
            "开始模型评估: task_id=%d, weights=%s, split=%s, device=%s",
            task_id,
            weights_path,
            split,
            eval_device,
        )

        try:
            from ultralytics import YOLO

            model = YOLO(str(weights_path))
            validation_stats = {}

            def capture_validation_stats(validator: object) -> None:
                """保存验证器统计；这些字段不会包含在 model.val() 的返回值中。"""
                validation_stats["nt_per_class"] = getattr(
                    validator, "nt_per_class", None
                )

            model.add_callback("on_val_end", capture_validation_stats)
            task_output_dir = _task_output_dir(task.task_uuid)
            results = model.val(
                data=str(data_yaml),
                split=split,
                conf=conf,
                iou=iou,
                imgsz=task.img_size,
                device=eval_device,
                save_json=True,
                plots=True,
                project=str(task_output_dir.parent),
                name=task_output_dir.name,
                exist_ok=True,
                verbose=False,
            )
            overall, per_class = _parse_evaluation_results(
                results,
                model.names,
                validation_stats.get("nt_per_class"),
            )

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
            db.flush()

            artifact_paths = _collect_evaluation_artifacts(
                task.task_uuid,
                signature,
                split=split,
                conf=conf,
                iou=iou,
            )
            evaluation_record = ModelEvaluation(
                training_task_id=task.id,
                model_version_id=model_version.id,
                weight_sha256=signature["weight_sha256"],
                dataset_fingerprint=signature["dataset_fingerprint"],
                split=split,
                conf=float(conf),
                iou=float(iou),
                imgsz=signature["imgsz"],
                overall=overall,
                per_class=per_class,
                artifact_paths=artifact_paths,
                evaluated_at=datetime.now(),
            )
            db.add(evaluation_record)
            db.commit()
            db.refresh(model_version)
            db.refresh(evaluation_record)

            report = {
                "task_id": task_id,
                "task_uuid": task.task_uuid,
                "split": split,
                "overall": overall,
                "per_class": per_class,
                "model_version_id": model_version.id,
                "model_version": model_version.version,
                "evaluation_id": evaluation_record.id,
                "evaluated_at": evaluation_record.evaluated_at.isoformat(),
                "cached": False,
                "artifacts": {
                    name: f"/api/training/validate/{task.id}/artifacts/{name}"
                    for name in artifact_paths
                },
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
    def start_validation(
        db,
        task_id: int,
        split: str = "val",
        conf: float = 0.001,
        iou: float = 0.6,
    ) -> dict:
        """在后台线程中启动模型评估，立即返回（前端轮询获取结果）。

        与训练相同的异步模式：接口只负责校验和登记，耗时的
        model.val() 在守护线程中执行，结果写入 _running_evaluations。
        """
        # ── 前置校验（复用 validate_model 的失败条件，尽早同步报错）──
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}
        if task.status != "completed":
            return {"error": f"训练任务状态为 {task.status}，只有已完成的任务才能评估"}

        cached_evaluation, signature = _find_cached_evaluation(
            db,
            task,
            split=split,
            conf=conf,
            iou=iou,
        )
        if cached_evaluation is not None:
            report = _evaluation_report(cached_evaluation, task)
            completed_at = (
                cached_evaluation.evaluated_at.isoformat()
                if cached_evaluation.evaluated_at
                else datetime.now().isoformat()
            )
            with _eval_lock:
                _running_evaluations[task_id] = {
                    "status": "completed",
                    "split": split,
                    "report": report,
                    "error": None,
                    "started_at": completed_at,
                    "completed_at": completed_at,
                    "thread": None,
                    "cached": True,
                }
            return {
                "task_id": task_id,
                "status": "completed",
                "split": split,
                "message": "已复用匹配的历史评估结果",
                "cached": True,
                "report": report,
            }
        if signature is None:
            weights_path = _task_output_dir(task.task_uuid) / "weights" / "best.pt"
            if not weights_path.exists():
                return {"error": f"模型权重不存在: {weights_path}"}
            return {"error": "data.yaml 不存在"}

        with _eval_lock:
            entry = _running_evaluations.get(task_id)
            if entry and entry["status"] == "running":
                return {"error": "该任务已有评估正在进行，请等待完成"}
            entry = {
                "status": "running",
                "split": split,
                "report": None,
                "error": None,
                "started_at": datetime.now().isoformat(),
                "completed_at": None,
                "thread": None,
                "cached": False,
            }
            _running_evaluations[task_id] = entry

        def _run_validation() -> None:
            """后台线程：执行评估并把结果写入注册表。"""
            # 后台线程不能复用请求级会话，需创建独立会话
            thread_db = SessionLocal()
            try:
                result = TrainingService.validate_model(
                    thread_db,
                    task_id=task_id,
                    split=split,
                    conf=conf,
                    iou=iou,
                )
            except Exception as e:  # validate_model 内部已兜底，这里防御线程裸奔
                logger.error(
                    "评估线程异常: task_id=%d, error=%s", task_id, str(e), exc_info=True
                )
                result = {"error": f"评估失败: {str(e)}"}
            finally:
                thread_db.close()

            with _eval_lock:
                current = _running_evaluations.get(task_id)
                if current is None:
                    return
                current["completed_at"] = datetime.now().isoformat()
                if "error" in result:
                    current["status"] = "failed"
                    current["error"] = result["error"]
                else:
                    current["status"] = "completed"
                    current["report"] = result

        thread = threading.Thread(
            target=_run_validation,
            daemon=True,
            name=f"validate-{task.task_uuid}",
        )
        entry["thread"] = thread
        thread.start()

        logger.info(
            "评估任务已启动: task_id=%d, uuid=%s, split=%s",
            task_id,
            task.task_uuid,
            split,
        )
        return {
            "task_id": task_id,
            "status": "running",
            "split": split,
            "message": "评估任务已启动，请轮询评估状态获取结果",
            "cached": False,
            "report": None,
        }

    @staticmethod
    def get_validation_status(task_id: int, db=None) -> dict:
        """查询后台评估状态；completed 时返回评估报告。

        注册表在进程内存中，服务重启后为空，此时返回 idle，
        前端可提示用户重新发起评估。
        """
        with _eval_lock:
            entry = _running_evaluations.get(task_id)
            if entry is None:
                if db is not None:
                    task = db.query(TrainingTask).filter(
                        TrainingTask.id == task_id
                    ).first()
                    latest = (
                        db.query(ModelEvaluation)
                        .filter(ModelEvaluation.training_task_id == task_id)
                        .order_by(
                            ModelEvaluation.evaluated_at.desc(),
                            ModelEvaluation.id.desc(),
                        )
                        .first()
                    )
                    if task is not None and latest is not None:
                        completed_at = (
                            latest.evaluated_at.isoformat()
                            if latest.evaluated_at
                            else None
                        )
                        return {
                            "task_id": task_id,
                            "status": "completed",
                            "split": latest.split,
                            "error": None,
                            "report": _evaluation_report(latest, task),
                            "started_at": completed_at,
                            "completed_at": completed_at,
                            "cached": True,
                        }
                return {"task_id": task_id, "status": "idle", "cached": False}
            return {
                "task_id": task_id,
                "status": entry["status"],
                "split": entry["split"],
                "error": entry["error"],
                "report": entry["report"],
                "started_at": entry["started_at"],
                "completed_at": entry["completed_at"],
                "cached": entry.get("cached", False),
            }

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
            evaluation_record = (
                db.query(ModelEvaluation)
                .filter(ModelEvaluation.id == evaluation.get("evaluation_id"))
                .first()
            )
            for plot_name, plot_path_value in (
                evaluation_record.artifact_paths.items()
                if evaluation_record and evaluation_record.artifact_paths
                else []
            ):
                plot_path = Path(plot_path_value)
                if plot_path.is_file():
                    shutil.copy2(plot_path, export_dir / plot_name)
            training_plot = task_output_dir / "results.png"
            if training_plot.is_file():
                shutil.copy2(training_plot, export_dir / training_plot.name)

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
            minio_object_name = None
            if upload_minio:
                try:
                    from app.storage.minio_client import MinIOClient

                    object_name = f"models/{scene.name}/{version}/best.pt"
                    minio_url = MinIOClient().upload_file(
                        object_name,
                        str(exported_weight),
                    )
                    minio_object_name = object_name
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
            model_version.minio_object_name = minio_object_name
            if minio_object_name:
                digest = hashlib.sha256()
                with exported_weight.open("rb") as model_file:
                    for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
                        digest.update(chunk)
                model_version.file_sha256 = digest.hexdigest()
                model_version.backed_up_at = datetime.now()
            model_version.map50 = overall["map50"]
            model_version.map50_95 = overall["map50_95"]
            model_version.precision = overall["precision"]
            model_version.recall = overall["recall"]
            model_version.per_class_ap = per_class
            model_version.file_size = exported_weight.stat().st_size
            model_version.description = description or f"训练任务 {task.task_uuid} 导出"
            # 重新导出代表该版本重新进入可管理状态，避免已删除记录导致后台备份失败。
            model_version.status = "active"
            model_version.archived_at = None
            model_version.deleted_at = None

            if set_default:
                db.query(ModelVersion).filter(
                    ModelVersion.scene_id == task.scene_id,
                    ModelVersion.id != model_version.id,
                ).update({"is_default": False}, synchronize_session=False)
                db.query(ModelVersion).filter(
                    ModelVersion.id != model_version.id,
                    ModelVersion.is_global_default.is_(True),
                ).update({"is_global_default": False}, synchronize_session=False)
                db.flush()
                model_version.is_default = True
                model_version.is_global_default = True

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
                "is_global_default": model_version.is_global_default,
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
    def rescan_tasks(db) -> dict:
        """扫描训练产物目录，将磁盘上存在但数据库缺失的任务重新登记。

        用于数据库重建后从磁盘恢复训练历史：遍历 ``runs/train/task_*`` 目录，
        读取每个目录下的 ``meta.json``（产物自描述），对数据库中不存在的
        ``task_uuid`` 执行 upsert，并从 ``results.csv`` 补录训练指标。

        恢复策略：
          - 仅恢复 meta.json 中 user_id / scene_id 仍能对应到现有记录的任务；
            归属用户不存在则跳过不恢复，找不到场景时按 scene_name 重新匹配。
          - 已存在（task_uuid 命中）的任务跳过，不覆盖现有数据。
          - 正在运行的任务不做恢复（进程内存中才有其真实状态）。

        Returns:
            统计字典：{"scanned", "recovered", "skipped", "failed", "details"}
        """
        root = _training_output_dir()
        result = {"scanned": 0, "recovered": 0, "skipped": 0, "failed": 0, "details": []}
        if not root.exists():
            logger.info("训练产物目录不存在，跳过恢复：%s", root)
            return result

        # 预取现有 task_uuid，避免逐目录查询
        existing_uuids = {
            row[0] for row in db.query(TrainingTask.task_uuid).all()
        }

        for task_dir in sorted(root.glob("task_*")):
            if not task_dir.is_dir():
                continue
            task_uuid = task_dir.name[len("task_"):]
            result["scanned"] += 1

            if task_uuid in existing_uuids:
                result["skipped"] += 1
                continue

            meta_path = task_dir / _META_FILENAME
            if not meta_path.exists():
                # 没有元数据无法可靠恢复归属，跳过（仅记录）
                result["skipped"] += 1
                result["details"].append(
                    {"task_uuid": task_uuid, "action": "skip", "reason": "缺少 meta.json"}
                )
                continue

            try:
                with meta_path.open("r", encoding="utf-8") as f:
                    meta = json.load(f)

                # ── 解析归属场景（优先 scene_id，回退 scene_name）──
                scene_id = meta.get("scene_id")
                scene = None
                if scene_id is not None:
                    scene = db.query(DetectionScene).filter(DetectionScene.id == scene_id).first()
                if scene is None and meta.get("scene_name"):
                    scene = (
                        db.query(DetectionScene)
                        .filter(DetectionScene.name == meta["scene_name"])
                        .first()
                    )
                if scene is None:
                    result["failed"] += 1
                    result["details"].append(
                        {"task_uuid": task_uuid, "action": "fail", "reason": "关联场景不存在"}
                    )
                    continue

                # ── 解析归属用户：user_id 必须仍对应现有用户，否则跳过不恢复 ──
                user_id = meta.get("user_id")
                user = None
                if user_id is not None:
                    user = db.query(User).filter(User.id == user_id).first()
                if user is None:
                    result["skipped"] += 1
                    result["details"].append(
                        {
                            "task_uuid": task_uuid,
                            "action": "skip",
                            "reason": "归属用户不存在，跳过恢复",
                        }
                    )
                    continue

                # ── 恢复状态：running/stopping 已中断，落库为 failed ──
                status = meta.get("status") or "completed"
                error_message = meta.get("error_message")
                if status in ("running", "stopping", "pending"):
                    status = "failed"
                    error_message = error_message or "服务重启导致训练中断，已从磁盘恢复"

                task = TrainingTask(
                    user_id=user.id,
                    scene_id=scene.id,
                    task_uuid=task_uuid,
                    status=status,
                    model_name=meta.get("model_name", "yolo11n"),
                    epochs=meta.get("epochs", 0) or 0,
                    img_size=meta.get("img_size", 640) or 640,
                    batch_size=meta.get("batch_size", 8) or 8,
                    device=meta.get("device", "cpu") or "cpu",
                    optimizer=meta.get("optimizer", "SGD") or "SGD",
                    lr0=meta.get("lr0", 0.01),
                    augment_config=meta.get("augment_config"),
                    current_epoch=meta.get("current_epoch", 0) or 0,
                    progress=meta.get("progress", 0) or 0,
                    dataset_path=meta.get("dataset_path"),
                    dataset_size=meta.get("dataset_size"),
                    data_yaml=meta.get("data_yaml"),
                    error_message=error_message,
                    created_at=_parse_meta_datetime(meta.get("created_at")),
                    started_at=_parse_meta_datetime(meta.get("started_at")),
                    completed_at=_parse_meta_datetime(meta.get("completed_at")),
                )
                db.add(task)
                db.commit()
                db.refresh(task)

                # ── 从 results.csv 补录训练指标曲线 ──
                results_csv = task_dir / "results.csv"
                if results_csv.exists():
                    for m in TrainingService.parse_results_csv(str(results_csv)):
                        db.add(TrainingMetric(task_id=task.id, **m))
                    db.commit()

                existing_uuids.add(task_uuid)
                result["recovered"] += 1
                result["details"].append(
                    {
                        "task_uuid": task_uuid,
                        "action": "recover",
                        "task_id": task.id,
                        "status": status,
                    }
                )
                logger.info(
                    "恢复训练任务：uuid=%s, task_id=%d, status=%s", task_uuid, task.id, status
                )
            except Exception as e:  # noqa: BLE001
                db.rollback()
                result["failed"] += 1
                result["details"].append(
                    {"task_uuid": task_uuid, "action": "fail", "reason": str(e)}
                )
                logger.warning("恢复训练任务失败：uuid=%s, error=%s", task_uuid, str(e))

        logger.info(
            "训练历史恢复完成：扫描 %d，恢复 %d，跳过 %d，失败 %d",
            result["scanned"],
            result["recovered"],
            result["skipped"],
            result["failed"],
        )
        return result

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


# 每个训练任务产物目录下的自描述元数据文件名。
# 有了它，即使数据库被重建，也能从磁盘完整恢复训练历史。
_META_FILENAME = "meta.json"

# meta.json 中持久化的 TrainingTask 字段（用于落盘与恢复）。
_META_TASK_FIELDS = (
    "task_uuid",
    "user_id",
    "scene_id",
    "status",
    "model_name",
    "epochs",
    "img_size",
    "batch_size",
    "device",
    "optimizer",
    "lr0",
    "augment_config",
    "current_epoch",
    "progress",
    "dataset_path",
    "dataset_size",
    "data_yaml",
    "error_message",
)

# meta.json 中以 ISO 字符串持久化的时间字段。
_META_DATETIME_FIELDS = ("created_at", "started_at", "completed_at")


def _write_task_meta(task: TrainingTask) -> None:
    """将训练任务元数据写入产物目录的 meta.json（产物自描述）。

    每当任务状态发生变化时调用，确保磁盘上始终保有可用于恢复的快照。
    失败不抛出——元数据落盘不应影响训练主流程。
    """
    try:
        task_dir = _task_output_dir(task.task_uuid)
        task_dir.mkdir(parents=True, exist_ok=True)

        meta = {field: getattr(task, field, None) for field in _META_TASK_FIELDS}
        for field in _META_DATETIME_FIELDS:
            value = getattr(task, field, None)
            meta[field] = value.isoformat() if value else None
        # 记录场景名，便于数据库重建后按名称（而非易变的自增 id）重新关联场景。
        scene = getattr(task, "scene", None)
        meta["scene_name"] = scene.name if scene else None
        meta["_meta_version"] = 1

        with (task_dir / _META_FILENAME).open("w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 — 元数据落盘失败不影响训练
        logger.warning("写入 meta.json 失败（不影响训练）：%s", str(e))


def _parse_meta_datetime(value):
    """将 meta.json 中的 ISO 字符串解析回 datetime，失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _relocate_under_backend(path_str: str):
    """将其它机器上的绝对路径按 datasets 锚点重定位到本地 backend 目录。

    云端/其它机器训练产生的 meta.json 里存的是当时的绝对路径（如
    /workspace/.../backend/datasets/rsod/yolo_dataset/data.yaml），在本机不存在。
    这里截取 ``datasets`` 及其之后的部分，拼到本地 backend 目录下重新定位。
    """
    if not path_str:
        return None
    parts = path_str.replace("\\", "/").split("/")
    if "datasets" in parts:
        idx = parts.index("datasets")
        candidate = _backend_dir() / Path(*parts[idx:])
        if candidate.exists():
            return candidate.resolve()
    return None


def _resolve_data_yaml(task: TrainingTask):
    """从任务记录中解析存在的 data.yaml 路径。

    兼容三种情况：同机训练（原样路径）、相对路径、以及跨机恢复
    （meta.json 中残留其它机器的绝对路径，需重定位到本地）。
    """
    candidates = []
    if task.data_yaml:
        candidates.append(str(task.data_yaml))
    if task.dataset_path:
        candidates.append(str(Path(task.dataset_path) / "data.yaml"))

    for candidate in candidates:
        path = Path(candidate)
        # 1. 原样路径（同机训练）
        if path.exists():
            return path.resolve()
        # 2. 相对路径回退到 backend 目录
        if not path.is_absolute():
            rooted = _backend_dir() / path
            if rooted.exists():
                return rooted.resolve()
        # 3. 跨机恢复：按 datasets 锚点重定位到本地 backend
        relocated = _relocate_under_backend(candidate)
        if relocated:
            return relocated

    # 4. 场景约定路径兜底：backend/datasets/{scene}/yolo_dataset/data.yaml
    scene = getattr(task, "scene", None)
    if scene is not None and scene.name:
        convention = (
            _backend_dir() / "datasets" / scene.name / "yolo_dataset" / "data.yaml"
        )
        if convention.exists():
            return convention.resolve()
    return None


_DATASET_FINGERPRINT_SUFFIXES = {
    ".yaml", ".yml", ".txt", ".jpg", ".jpeg", ".png", ".bmp", ".webp"
}
_EVALUATION_ARTIFACT_NAMES = (
    "confusion_matrix.png",
    "confusion_matrix_normalized.png",
    "F1_curve.png",
    "P_curve.png",
    "R_curve.png",
    "PR_curve.png",
    "predictions.json",
)


def _sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_fingerprint(data_yaml: Path) -> str:
    """根据数据配置、标签内容和图像元数据生成稳定的数据集指纹。"""
    root = data_yaml.parent.resolve()
    digest = hashlib.sha256()
    digest.update(data_yaml.read_bytes())
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.suffix.lower() not in _DATASET_FINGERPRINT_SUFFIXES:
            continue
        relative_path = path.relative_to(root)
        if any(
            part.startswith("task_") or part in {"runs", "models"}
            for part in relative_path.parts
        ):
            continue
        relative = relative_path.as_posix()
        stat = path.stat()
        digest.update(relative.encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        # 标签与 YAML 较小，额外哈希内容，避免时间戳被保留时漏判修改。
        if path.suffix.lower() in {".txt", ".yaml", ".yml"}:
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _evaluation_signature(task: TrainingTask) -> dict | None:
    """返回当前权重和数据集对应的完整评估指纹。"""
    weights_path = _task_output_dir(task.task_uuid) / "weights" / "best.pt"
    data_yaml = _resolve_data_yaml(task)
    if not weights_path.exists() or not data_yaml:
        return None
    return {
        "weights_path": weights_path,
        "data_yaml": data_yaml,
        "weight_sha256": _sha256_file(weights_path),
        "dataset_fingerprint": _dataset_fingerprint(data_yaml),
        "imgsz": int(task.img_size),
    }


def _collect_evaluation_artifacts(
    task_uuid: str,
    signature: dict,
    *,
    split: str,
    conf: float,
    iou: float,
) -> dict[str, str]:
    """把本次评估图表快照到独立目录，避免后续评估覆盖缓存产物。"""
    task_dir = _task_output_dir(task_uuid)
    cache_key = hashlib.sha256(
        json.dumps(
            {
                "weight_sha256": signature["weight_sha256"],
                "dataset_fingerprint": signature["dataset_fingerprint"],
                "split": split,
                "conf": float(conf),
                "iou": float(iou),
                "imgsz": signature["imgsz"],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:24]
    snapshot_dir = task_dir / "evaluations" / cache_key
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        task_dir / name
        for name in _EVALUATION_ARTIFACT_NAMES
        if (task_dir / name).is_file()
    ]
    for path in sorted(task_dir.glob("val_batch*_pred.jpg")):
        sources.append(path)
    artifacts = {}
    for source in sources:
        target = snapshot_dir / source.name
        shutil.copy2(source, target)
        artifacts[source.name] = str(target)
    return artifacts


def _evaluation_report(item: ModelEvaluation, task: TrainingTask) -> dict:
    """将持久化评估记录转换为 API 报告。"""
    artifacts = {
        name: f"/api/training/validate/{task.id}/artifacts/{name}"
        for name, path in (item.artifact_paths or {}).items()
        if Path(path).is_file()
    }
    return {
        "task_id": task.id,
        "task_uuid": task.task_uuid,
        "split": item.split,
        "overall": item.overall,
        "per_class": item.per_class,
        "model_version_id": item.model_version_id,
        "model_version": (
            item.model_version.version if item.model_version is not None else None
        ),
        "evaluation_id": item.id,
        "evaluated_at": item.evaluated_at.isoformat() if item.evaluated_at else None,
        "cached": True,
        "artifacts": artifacts,
    }


def _find_cached_evaluation(
    db,
    task: TrainingTask,
    *,
    split: str,
    conf: float,
    iou: float,
    signature: dict | None = None,
) -> tuple[ModelEvaluation | None, dict | None]:
    """按完整评估条件查找可复用记录。"""
    signature = signature or _evaluation_signature(task)
    if signature is None:
        return None, None
    item = (
        db.query(ModelEvaluation)
        .filter(
            ModelEvaluation.training_task_id == task.id,
            ModelEvaluation.weight_sha256 == signature["weight_sha256"],
            ModelEvaluation.dataset_fingerprint == signature["dataset_fingerprint"],
            ModelEvaluation.split == split,
            ModelEvaluation.conf == float(conf),
            ModelEvaluation.iou == float(iou),
            ModelEvaluation.imgsz == signature["imgsz"],
        )
        .order_by(ModelEvaluation.evaluated_at.desc(), ModelEvaluation.id.desc())
        .first()
    )
    return item, signature


def _get_augmentation_kwargs(augment_config) -> dict:
    """过滤并返回 Ultralytics 支持的数据增强参数。"""
    if not isinstance(augment_config, dict):
        return {}
    return {
        key: value
        for key, value in augment_config.items()
        if key in _AUGMENTATION_KEYS and value is not None
    }


def _resolve_eval_device(train_device) -> str:
    """评估设备跟随训练设备，GPU 不可用时回退 CPU。

    训练记录里的 device 可能是 "cpu"、"0"、"1" 或 "cuda:0"；
    评估机器不一定有训练时的 GPU（如云端训练、本地评估），
    因此先检查 CUDA 可用性再决定是否沿用。
    """
    device = str(train_device or "cpu").strip().lower()
    if device in ("", "cpu"):
        return "cpu"

    try:
        import torch

        if not torch.cuda.is_available():
            logger.warning("训练设备 %s 不可用（无 CUDA），评估回退到 CPU", train_device)
            return "cpu"
        # "0" / "cuda:0" → 校验显卡编号存在
        index_part = device.split(":")[-1]
        if index_part.isdigit() and int(index_part) >= torch.cuda.device_count():
            logger.warning(
                "训练设备 %s 超出本机 GPU 数量（%d），评估回退到 CPU",
                train_device,
                torch.cuda.device_count(),
            )
            return "cpu"
        return str(train_device)
    except Exception as e:
        logger.warning("检测 GPU 可用性失败（%s），评估回退到 CPU", str(e))
        return "cpu"


def _class_name(class_names, class_id: int) -> str:
    if isinstance(class_names, dict):
        return class_names.get(class_id, f"class_{class_id}")
    if class_id < len(class_names):
        return class_names[class_id]
    return f"class_{class_id}"


def _parse_evaluation_results(
    results,
    class_names,
    instance_values=None,
) -> tuple[dict, dict]:
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

    if instance_values is None:
        instance_values = getattr(results, "nt_per_class", None)
    if instance_values is None:
        # 兼容旧版或测试替身曾将统计值挂在 box 上的情况。
        instance_values = getattr(box, "nt_per_class", None)

    ap_class_ids = getattr(box, "ap_class_index", None)
    if ap_class_ids is None:
        ap_class_ids = range(len(ap50_values))

    for metric_index, ap50 in enumerate(ap50_values):
        class_id = int(ap_class_ids[metric_index])
        metrics = {
            "ap50": round(float(ap50), 4),
            "ap50_95": round(float(ap_values[metric_index]), 4),
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
