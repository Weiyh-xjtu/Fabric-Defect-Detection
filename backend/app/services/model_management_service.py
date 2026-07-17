"""全局模型版本管理与测试服务。"""

import base64
import os
from pathlib import Path

import cv2
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from ultralytics import YOLO

from app.core.logger import get_logger
from app.entity.db_models import DetectionTask, ModelVersion


logger = get_logger(__name__)


class ModelManagementService:
    """管理模型版本，并维护检测服务唯一的全局启用模型。"""

    @staticmethod
    def _existing_model_path(model_version: ModelVersion) -> str:
        """返回可用的模型路径；配置异常时给出明确错误。"""
        model_path = os.path.abspath(model_version.model_path)
        if model_version.status != "active":
            raise RuntimeError(f"模型版本 {model_version.version} 当前状态为 {model_version.status}")
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"模型权重不存在: {model_path}")
        return model_path

    def get_global_model_version(
        self,
        db: Session,
        *,
        auto_select: bool = True,
    ) -> ModelVersion | None:
        """获取全局启用模型；首次升级时自动选择最新的可用版本。"""
        selected = (
            db.query(ModelVersion)
            .options(joinedload(ModelVersion.scene))
            .filter(ModelVersion.is_global_default.is_(True))
            .first()
        )
        if selected is not None:
            self._existing_model_path(selected)
            return selected
        if not auto_select:
            return None

        candidates = (
            db.query(ModelVersion)
            .options(joinedload(ModelVersion.scene))
            .filter(ModelVersion.status == "active")
            .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
            .all()
        )
        selected = next(
            (item for item in candidates if os.path.isfile(os.path.abspath(item.model_path))),
            None,
        )
        if selected is None:
            return None

        db.query(ModelVersion).filter(
            ModelVersion.is_global_default.is_(True)
        ).update({"is_global_default": False}, synchronize_session=False)
        db.flush()
        selected.is_global_default = True
        db.commit()
        db.refresh(selected)
        logger.info(
            "自动初始化全局检测模型: model_version_id=%d, version=%s",
            selected.id,
            selected.version,
        )
        return selected

    def activate_global_model(self, db: Session, model_version_id: int) -> ModelVersion:
        """将指定可用版本切换为全局检测模型。"""
        target = (
            db.query(ModelVersion)
            .options(joinedload(ModelVersion.scene))
            .filter(ModelVersion.id == model_version_id)
            .first()
        )
        if target is None:
            raise LookupError("模型版本不存在")
        self._existing_model_path(target)

        db.query(ModelVersion).filter(
            ModelVersion.is_global_default.is_(True),
            ModelVersion.id != target.id,
        ).update({"is_global_default": False}, synchronize_session=False)
        db.flush()
        target.is_global_default = True
        db.commit()
        db.refresh(target)
        logger.info(
            "全局检测模型已切换: model_version_id=%d, version=%s, path=%s",
            target.id,
            target.version,
            target.model_path,
        )
        return target

    @staticmethod
    def serialize_model_version(
        db: Session,
        item: ModelVersion,
        detection_task_count: int | None = None,
    ) -> dict:
        """生成前端模型管理页面所需的版本信息。"""
        scene = item.scene
        return {
            "id": item.id,
            "scene_id": item.scene_id,
            "scene_name": scene.display_name if scene else None,
            "training_task_id": item.training_task_id,
            "version": item.version,
            "model_name": item.model_name,
            "model_type": item.model_type,
            "status": item.status,
            "model_path": item.model_path,
            "minio_url": item.minio_url,
            "map50": item.map50,
            "map50_95": item.map50_95,
            "precision": item.precision,
            "recall": item.recall,
            "per_class_ap": item.per_class_ap,
            "description": item.description,
            "file_size": item.file_size,
            "is_default": bool(item.is_default),
            "is_global_default": bool(item.is_global_default),
            "file_exists": Path(item.model_path).is_file(),
            "detection_task_count": (
                detection_task_count
                if detection_task_count is not None
                else db.query(DetectionTask)
                .filter(DetectionTask.model_version_id == item.id)
                .count()
            ),
            "created_at": item.created_at,
        }

    def list_model_versions(
        self,
        db: Session,
        *,
        scene_id: int | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """按创建时间倒序返回模型版本。"""
        query = db.query(ModelVersion).options(joinedload(ModelVersion.scene))
        if scene_id is not None:
            query = query.filter(ModelVersion.scene_id == scene_id)
        if status:
            query = query.filter(ModelVersion.status == status)
        items = query.order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).all()
        if not items:
            return []
        task_counts = {
            model_version_id: int(count)
            for model_version_id, count in (
                db.query(
                    DetectionTask.model_version_id,
                    func.count(DetectionTask.id),
                )
                .filter(DetectionTask.model_version_id.in_([item.id for item in items]))
                .group_by(DetectionTask.model_version_id)
                .all()
            )
        }
        return [
            self.serialize_model_version(db, item, task_counts.get(item.id, 0))
            for item in items
        ]

    def predict_image(
        self,
        model_version: ModelVersion,
        image_path: str,
        *,
        filename: str | None,
        conf: float,
        iou: float,
    ) -> dict:
        """使用指定模型版本测试单张图片，不创建正式检测任务。"""
        model_path = self._existing_model_path(model_version)
        model = YOLO(model_path)
        results = model.predict(
            source=image_path,
            conf=conf,
            iou=iou,
            imgsz=640,
            device="cpu",
            save=False,
            verbose=False,
        )
        if not results:
            raise RuntimeError("模型未返回预测结果")

        result = results[0]
        detections: list[dict] = []
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
                        "bbox": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                    }
                )

        encoded, buffer = cv2.imencode(
            ".jpg",
            result.plot(),
            [cv2.IMWRITE_JPEG_QUALITY, 85],
        )
        if not encoded:
            raise RuntimeError("标注图片编码失败")

        class_counts: dict[str, int] = {}
        for detection in detections:
            class_name = detection["class_name"]
            class_counts[class_name] = class_counts.get(class_name, 0) + 1

        return {
            "model_version_id": model_version.id,
            "model_version": model_version.version,
            "filename": filename,
            "total_objects": len(detections),
            "detections": detections,
            "class_counts": class_counts,
            "annotated_image": base64.b64encode(buffer).decode("utf-8"),
            "inference_time": round(float(result.speed.get("inference", 0)), 2),
        }


model_management_service = ModelManagementService()
