"""检测历史记录查询与删除服务。"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import String, cast, desc, func, or_
from sqlalchemy.orm import Session, joinedload

from app.entity.db_models import DetectionResult, DetectionScene, DetectionTask, User
from app.storage.minio_client import MinIOClient


class HistoryService:
    """提供指定用户或全厂检测任务的分页、详情与统计能力。"""

    @staticmethod
    def list_tasks(
        db: Session,
        user_id: int | None,
        page: int = 1,
        page_size: int = 10,
        task_type: str | None = None,
        status: str | None = None,
        scene_id: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        keyword: str | None = None,
    ) -> dict:
        """分页查询当前用户的检测任务。"""
        query = (
            db.query(DetectionTask)
            .outerjoin(DetectionScene, DetectionTask.scene_id == DetectionScene.id)
            .outerjoin(User, DetectionTask.user_id == User.id)
            .options(
                joinedload(DetectionTask.scene),
                joinedload(DetectionTask.user),
                joinedload(DetectionTask.model_version),
            )
        )
        if user_id is not None:
            query = query.filter(DetectionTask.user_id == user_id)
        if task_type:
            query = query.filter(DetectionTask.task_type == task_type)
        if status:
            query = query.filter(DetectionTask.status == status)
        if scene_id is not None:
            query = query.filter(DetectionTask.scene_id == scene_id)
        if start_date:
            query = query.filter(
                DetectionTask.created_at >= datetime.combine(start_date, time.min)
            )
        if end_date:
            query = query.filter(
                DetectionTask.created_at
                < datetime.combine(end_date + timedelta(days=1), time.min)
            )
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            query = query.filter(
                or_(
                    cast(DetectionTask.id, String).ilike(pattern),
                    DetectionScene.name.ilike(pattern),
                    DetectionScene.display_name.ilike(pattern),
                    User.username.ilike(pattern),
                    User.email.ilike(pattern),
                )
            )

        total = query.count()
        tasks = (
            query.order_by(desc(DetectionTask.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "items": [HistoryService._serialize_task(task) for task in tasks],
        }

    @staticmethod
    def _serialize_task(task: DetectionTask) -> dict:
        """序列化检测任务列表项。"""
        return {
            "id": task.id,
            "task_type": task.task_type,
            "status": task.status,
            "scene_id": task.scene_id,
            "scene_name": task.scene.display_name if task.scene else None,
            "model_version_id": task.model_version_id,
            "model_version": (
                task.model_version.version if task.model_version else None
            ),
            "model_name": (
                task.model_version.model_name if task.model_version else None
            ),
            "initiator_user_id": task.user_id,
            "initiator_username": task.user.username if task.user else None,
            "total_images": int(task.total_images or 0),
            "total_objects": int(task.total_objects or 0),
            "total_inference_time": round(float(task.total_inference_time or 0), 2),
            "conf_threshold": task.conf_threshold,
            "iou_threshold": task.iou_threshold,
            "error_message": task.error_message,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "completed_at": (
                task.completed_at.isoformat() if task.completed_at else None
            ),
        }

    @staticmethod
    def get_task_detail(db: Session, user_id: int | None, task_id: int) -> dict | None:
        """获取当前用户的任务详情和全部检测结果。"""
        task = (
            db.query(DetectionTask)
            .options(
                joinedload(DetectionTask.scene),
                joinedload(DetectionTask.user),
                joinedload(DetectionTask.model_version),
            )
            .filter(DetectionTask.id == task_id)
            .first()
        )
        if task and user_id is not None and task.user_id != user_id:
            return None
        if not task:
            return None

        results = (
            db.query(DetectionResult)
            .filter(DetectionResult.task_id == task_id)
            .order_by(DetectionResult.id)
            .all()
        )
        # 场景表实时中文名优先，结果行内的历史快照兜底，保证改名后展示跟随。
        scene = task.scene
        cn_map = (
            scene.class_names_cn
            if scene and isinstance(scene.class_names_cn, dict)
            else {}
        )
        class_counts: dict[str, int] = {}
        for result in results:
            display_name = (
                cn_map.get(result.class_name)
                or result.class_name_cn
                or result.class_name
            )
            class_counts[display_name] = class_counts.get(display_name, 0) + 1

        minio = MinIOClient(ensure_bucket=False)

        return {
            "task": HistoryService._serialize_task(task),
            "class_counts": class_counts,
            "results": [
                {
                    "id": result.id,
                    "class_name": result.class_name,
                    "class_name_cn": cn_map.get(result.class_name)
                    or result.class_name_cn,
                    "class_id": result.class_id,
                    "confidence": round(float(result.confidence), 4),
                    "bbox": result.bbox,
                    "image_path": result.image_path,
                    "annotated_image_url": minio.browser_url_from_url_or_name(
                        result.annotated_image_url,
                        filename=result.image_path or "annotated.jpg",
                        content_type="image/jpeg",
                    ),
                    "inference_time": (
                        round(float(result.inference_time), 2)
                        if result.inference_time is not None
                        else None
                    ),
                    "image_width": result.image_width,
                    "image_height": result.image_height,
                    "created_at": (
                        result.created_at.isoformat() if result.created_at else None
                    ),
                }
                for result in results
            ],
        }

    @staticmethod
    def delete_task(db: Session, user_id: int | None, task_id: int) -> bool:
        """删除当前用户的任务；ORM 关系会级联删除结果。"""
        task = (
            db.query(DetectionTask)
            .filter(DetectionTask.id == task_id)
            .first()
        )
        if task and user_id is not None and task.user_id != user_id:
            return False
        if not task:
            return False
        db.delete(task)
        db.commit()
        return True

    @staticmethod
    def get_summary(db: Session, user_id: int | None) -> dict:
        """返回总任务数、今日任务数和状态分布。"""
        today_start = datetime.combine(date.today(), time.min)
        total_query = db.query(func.count(DetectionTask.id))
        today_query = db.query(func.count(DetectionTask.id)).filter(
            DetectionTask.created_at >= today_start
        )
        status_query = db.query(DetectionTask.status, func.count(DetectionTask.id))
        if user_id is not None:
            total_query = total_query.filter(DetectionTask.user_id == user_id)
            today_query = today_query.filter(DetectionTask.user_id == user_id)
            status_query = status_query.filter(DetectionTask.user_id == user_id)
        total = total_query.scalar() or 0
        today_tasks = today_query.scalar() or 0
        rows = status_query.group_by(DetectionTask.status).all()
        status_counts = {
            "completed": 0,
            "processing": 0,
            "failed": 0,
            "pending": 0,
        }
        status_counts.update({status: int(count) for status, count in rows})
        return {
            "total_tasks": int(total),
            "today_tasks": int(today_tasks),
            "status_counts": status_counts,
        }

    @staticmethod
    def list_scenes(db: Session) -> list[dict]:
        """返回全部启用的检测场景。"""
        scenes = (
            db.query(DetectionScene)
            .filter(DetectionScene.is_active.is_(True))
            .order_by(DetectionScene.display_name)
            .all()
        )
        return [
            {
                "id": scene.id,
                "name": scene.name,
                "display_name": scene.display_name,
                "category": scene.category,
            }
            for scene in scenes
        ]


history_service = HistoryService()
