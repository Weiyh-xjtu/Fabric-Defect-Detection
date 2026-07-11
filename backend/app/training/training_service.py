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
import os
import threading
import uuid
from datetime import datetime

from app.config.settings import settings
from app.core.logger import get_logger
from app.database.session import SessionLocal
from app.entity.db_models import TrainingMetric, TrainingTask

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

            # ── 注册训练回调：每个 epoch 结束时更新数据库 ──
            def on_train_epoch_end(trainer):
                """训练 epoch 结束时的回调"""
                try:
                    # 从 trainer 获取当前 epoch 指标
                    epoch = trainer.epoch + 1  # ultralytics epoch 从 0 开始
                    metrics = trainer.metrics or {}
                    loss_items = (
                        trainer.loss_items if hasattr(trainer, "loss_items") else {}
                    )

                    metric_record = TrainingMetric(
                        task_id=task_id,
                        epoch=epoch,
                        box_loss=float(
                            metrics.get("metrics/box_loss", 0)
                            if isinstance(metrics, dict)
                            else 0
                        ),
                        cls_loss=float(
                            metrics.get("metrics/cls_loss", 0)
                            if isinstance(metrics, dict)
                            else 0
                        ),
                        dfl_loss=float(
                            metrics.get("metrics/dfl_loss", 0)
                            if isinstance(metrics, dict)
                            else 0
                        ),
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
            existing_epochs = set()
            existing = (
                db.query(TrainingMetric).filter(TrainingMetric.task_id == task_id).all()
            )
            for m in existing:
                existing_epochs.add(m.epoch)

            # 解析 results.csv
            with open(results_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # results.csv 的列名可能带空格
                    row = {k.strip(): v.strip() for k, v in row.items()}
                    epoch = int(row.get("epoch", 0))  # Ultralytics CSV 中 epoch 从 1 开始

                    # 跳过已存在的 epoch（回调已写入）
                    if epoch in existing_epochs:
                        continue

                    metric = TrainingMetric(
                        task_id=task_id,
                        epoch=epoch,
                        box_loss=_safe_float(row.get("train/box_loss", "")),
                        cls_loss=_safe_float(row.get("train/cls_loss", "")),
                        dfl_loss=_safe_float(row.get("train/dfl_loss", "")),
                        precision=_safe_float(row.get("metrics/precision(B)", "")),
                        recall=_safe_float(row.get("metrics/recall(B)", "")),
                        map50=_safe_float(row.get("metrics/mAP50(B)", "")),
                        map50_95=_safe_float(row.get("metrics/mAP50-95(B)", "")),
                        lr=_safe_float(row.get("lr/pg0", "")),
                    )
                    db.add(metric)

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