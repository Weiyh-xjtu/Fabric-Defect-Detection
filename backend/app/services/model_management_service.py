"""全局模型版本管理、归档删除与 MinIO 备份恢复服务。"""

import base64
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

import cv2
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from ultralytics import YOLO

from app.core.logger import get_logger
from app.entity.db_models import DetectionTask, ModelVersion
from app.storage.minio_client import MinIOClient


logger = get_logger(__name__)


class ModelManagementService:
    """管理模型版本，并维护检测服务唯一的全局启用模型。"""

    @staticmethod
    def _backend_dir() -> Path:
        return Path(__file__).resolve().parents[2]

    @classmethod
    def _models_dir(cls) -> Path:
        return cls._backend_dir() / "models"

    @staticmethod
    def _safe_segment(value: str) -> str:
        """生成可用于本地目录和 MinIO 对象名的稳定片段。"""
        normalized = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
        return normalized.strip("._") or "model"

    @staticmethod
    def _sha256(file_path: str | Path) -> str:
        """流式计算模型文件 SHA-256，避免大权重整体读入内存。"""
        digest = hashlib.sha256()
        with Path(file_path).open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _backup_object_name(cls, item: ModelVersion) -> str:
        scene_name = item.scene.name if item.scene else f"scene_{item.scene_id}"
        return (
            f"models/{cls._safe_segment(scene_name)}/"
            f"{cls._safe_segment(item.version)}/best.pt"
        )

    @classmethod
    def _restore_target_path(cls, item: ModelVersion) -> Path:
        scene_name = item.scene.name if item.scene else f"scene_{item.scene_id}"
        directory = (
            cls._models_dir()
            / f"{cls._safe_segment(scene_name)}_{cls._safe_segment(item.version)}"
        )
        return directory / "best.pt"

    @staticmethod
    def _resolve_object_name(item: ModelVersion, client: MinIOClient) -> str | None:
        if item.minio_object_name:
            return item.minio_object_name
        if item.minio_url:
            return client.object_name_from_url(item.minio_url)
        return None

    def ensure_model_available(
        self,
        db: Session,
        item: ModelVersion,
        *,
        require_active: bool = True,
        auto_restore: bool = True,
    ) -> str:
        """返回本地权重路径；本地缺失且有备份时自动从 MinIO 恢复。"""
        if require_active and item.status != "active":
            raise RuntimeError(f"模型版本 {item.version} 当前状态为 {item.status}")
        if item.status == "deleted":
            raise RuntimeError(f"模型版本 {item.version} 已删除")

        model_path = os.path.abspath(item.model_path)
        if os.path.isfile(model_path):
            return model_path
        if auto_restore and (item.minio_object_name or item.minio_url):
            restored = self.restore_model(db, item)
            return os.path.abspath(restored.model_path)
        raise FileNotFoundError(f"模型权重不存在且没有可用备份: {model_path}")

    def get_global_model_version(
        self,
        db: Session,
        *,
        auto_select: bool = True,
    ) -> ModelVersion | None:
        """获取全局启用模型；本地文件缺失时自动从 MinIO 恢复。"""
        selected = (
            db.query(ModelVersion)
            .options(joinedload(ModelVersion.scene))
            .filter(ModelVersion.is_global_default.is_(True))
            .first()
        )
        if selected is not None:
            self.ensure_model_available(db, selected)
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
        selected = None
        for candidate in candidates:
            try:
                self.ensure_model_available(db, candidate)
                selected = candidate
                break
            except (FileNotFoundError, RuntimeError) as exc:
                logger.warning("跳过不可用模型版本 %s: %s", candidate.version, exc)
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
        target = self.get_model_version(db, model_version_id)
        self.ensure_model_available(db, target)

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
    def get_model_version(db: Session, model_version_id: int) -> ModelVersion:
        """返回带场景信息的模型版本。"""
        item = (
            db.query(ModelVersion)
            .options(joinedload(ModelVersion.scene))
            .filter(ModelVersion.id == model_version_id)
            .first()
        )
        if item is None:
            raise LookupError("模型版本不存在")
        return item

    def archive_model(self, db: Session, model_version_id: int) -> ModelVersion:
        """归档非全局模型；保留本地文件、MinIO 备份和历史关联。"""
        item = self.get_model_version(db, model_version_id)
        if item.is_global_default:
            raise RuntimeError("当前全局模型不能归档，请先切换到其他版本")
        if item.status == "deleted":
            raise RuntimeError("已删除的模型不能归档")
        if item.status == "archived":
            return item
        item.status = "archived"
        item.is_default = False
        item.archived_at = datetime.now()
        db.commit()
        db.refresh(item)
        return item

    def unarchive_model(self, db: Session, model_version_id: int) -> ModelVersion:
        """将归档模型恢复为可用状态，不自动切换为全局模型。"""
        item = self.get_model_version(db, model_version_id)
        if item.status != "archived":
            raise RuntimeError("只有已归档模型可以恢复为启用状态")
        item.status = "active"
        item.archived_at = None
        db.commit()
        db.refresh(item)
        return item

    def backup_model(self, db: Session, model_version_id: int) -> ModelVersion:
        """将本地权重上传到 MinIO，并保存永久对象名与内容校验值。"""
        item = self.get_model_version(db, model_version_id)
        local_path = self.ensure_model_available(
            db,
            item,
            require_active=False,
            auto_restore=False,
        )
        object_name = self._backup_object_name(item)
        client = MinIOClient()
        minio_url = client.upload_file(object_name, local_path)
        item.minio_object_name = object_name
        item.minio_url = minio_url
        item.file_sha256 = self._sha256(local_path)
        item.file_size = os.path.getsize(local_path)
        item.backed_up_at = datetime.now()
        db.commit()
        db.refresh(item)
        logger.info(
            "模型已备份到 MinIO: model_version_id=%d, object=%s",
            item.id,
            object_name,
        )
        return item

    def restore_model(self, db: Session, item: ModelVersion) -> ModelVersion:
        """从 MinIO 原子恢复到 backend/models，并校验 SHA-256。"""
        if item.status == "deleted":
            raise RuntimeError("已删除模型的 MinIO 对象已清理，不能恢复")
        client = MinIOClient()
        object_name = self._resolve_object_name(item, client)
        if not object_name:
            raise RuntimeError("模型没有可用的 MinIO 备份")

        target_path = self._restore_target_path(item)
        client.download_file(object_name, str(target_path))
        actual_sha256 = self._sha256(target_path)
        if item.file_sha256 and actual_sha256 != item.file_sha256:
            target_path.unlink(missing_ok=True)
            raise RuntimeError("MinIO 恢复文件校验失败，SHA-256 与备份记录不一致")

        item.model_path = str(target_path)
        item.minio_object_name = object_name
        item.minio_url = client.get_presigned_url(object_name)
        item.file_sha256 = actual_sha256
        item.file_size = target_path.stat().st_size
        if item.backed_up_at is None:
            item.backed_up_at = datetime.now()
        db.commit()
        db.refresh(item)
        logger.info(
            "模型已从 MinIO 恢复: model_version_id=%d, target=%s",
            item.id,
            target_path,
        )
        return item

    @classmethod
    def _delete_exported_local_copy(cls, model_path: str) -> str:
        """仅删除 backend/models 内文件，训练任务目录中的权重永远保留。"""
        path = Path(model_path).resolve()
        models_dir = cls._models_dir().resolve()
        try:
            path.relative_to(models_dir)
        except ValueError:
            return "retained_training_artifact" if path.is_file() else "not_found"
        if not path.is_file():
            return "not_found"

        path.unlink()
        parent = path.parent
        while parent != models_dir:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return "deleted_export_copy"

    def delete_model(self, db: Session, model_version_id: int) -> dict:
        """软删除版本，并安全清理正式副本与 MinIO 对象。"""
        item = self.get_model_version(db, model_version_id)
        if item.is_global_default:
            raise RuntimeError("当前全局模型不能删除，请先切换到其他版本")
        if item.status == "deleted":
            raise RuntimeError("模型版本已经删除")

        minio_action = "not_configured"
        if item.minio_object_name or item.minio_url:
            client = MinIOClient()
            object_name = self._resolve_object_name(item, client)
            if object_name:
                client.delete_file(object_name)
                minio_action = "deleted"

        local_action = self._delete_exported_local_copy(item.model_path)
        item.status = "deleted"
        item.is_default = False
        item.archived_at = None
        item.deleted_at = datetime.now()
        item.minio_object_name = None
        item.minio_url = None
        item.backed_up_at = None
        db.commit()
        db.refresh(item)
        logger.info(
            "模型版本已软删除: id=%d, local=%s, minio=%s",
            item.id,
            local_action,
            minio_action,
        )
        return {
            "model": item,
            "local_action": local_action,
            "minio_action": minio_action,
        }

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
            "minio_object_name": item.minio_object_name,
            "file_sha256": item.file_sha256,
            "backed_up_at": item.backed_up_at,
            "backup_available": bool(item.minio_object_name or item.minio_url),
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
            "archived_at": item.archived_at,
            "deleted_at": item.deleted_at,
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
        db: Session,
        model_version: ModelVersion,
        image_path: str,
        *,
        filename: str | None,
        conf: float,
        iou: float,
    ) -> dict:
        """使用指定模型版本测试单张图片，不创建正式检测任务。"""
        model_path = self.ensure_model_available(db, model_version)
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
