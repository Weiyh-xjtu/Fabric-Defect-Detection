"""数据看板聚合统计服务。"""

from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.entity.db_models import DetectionResult, DetectionScene, DetectionTask


class DashboardService:
    """按当前用户聚合检测任务数据。"""

    @staticmethod
    def _calc_growth(current: float, previous: float) -> float:
        """计算当前周期相对上一周期的增长率。"""
        if previous == 0:
            return 100.0 if current > 0 else 0.0
        return round((current - previous) / previous * 100, 1)

    @staticmethod
    def _statistics_for_period(
        db: Session,
        user_id: int,
        start_at: datetime,
        end_at: datetime,
    ) -> object:
        """查询一个左闭右开时间段内的汇总数据。"""
        return (
            db.query(
                func.count(DetectionTask.id).label("total_tasks"),
                func.coalesce(func.sum(DetectionTask.total_images), 0).label(
                    "total_images"
                ),
                func.coalesce(func.sum(DetectionTask.total_objects), 0).label(
                    "total_objects"
                ),
                func.coalesce(func.avg(DetectionTask.total_inference_time), 0).label(
                    "avg_inference_time"
                ),
            )
            .filter(
                DetectionTask.user_id == user_id,
                DetectionTask.created_at >= start_at,
                DetectionTask.created_at < end_at,
            )
            .one()
        )

    def get_statistics(self, db: Session, user_id: int, days: int = 30) -> dict:
        """返回任务、图片、目标、平均耗时及环比增长率。"""
        now = datetime.now()
        current_start = now - timedelta(days=days)
        previous_start = current_start - timedelta(days=days)
        current = self._statistics_for_period(db, user_id, current_start, now)
        previous = self._statistics_for_period(
            db, user_id, previous_start, current_start
        )

        current_tasks = int(current.total_tasks or 0)
        current_images = int(current.total_images or 0)
        current_objects = int(current.total_objects or 0)
        current_time = float(current.avg_inference_time or 0)
        previous_tasks = int(previous.total_tasks or 0)
        previous_images = int(previous.total_images or 0)
        previous_objects = int(previous.total_objects or 0)
        previous_time = float(previous.avg_inference_time or 0)

        return {
            "total_tasks": current_tasks,
            "total_images": current_images,
            "total_objects": current_objects,
            "avg_inference_time": round(current_time, 2),
            "growth": {
                "tasks": self._calc_growth(current_tasks, previous_tasks),
                "images": self._calc_growth(current_images, previous_images),
                "objects": self._calc_growth(current_objects, previous_objects),
                "inference_time": self._calc_growth(current_time, previous_time),
            },
            "period_days": days,
        }

    @staticmethod
    def get_trend(db: Session, user_id: int, days: int = 30) -> dict:
        """返回包含空白日期补零的每日检测趋势。"""
        today = date.today()
        first_day = today - timedelta(days=days - 1)
        start_at = datetime.combine(first_day, datetime.min.time())
        # func.date 在 PostgreSQL 返回 date、在 SQLite 返回 ISO 字符串，
        # 两端都不会触发 SQLAlchemy Date 类型处理器的兼容问题。
        day_expression = func.date(DetectionTask.created_at)
        rows = (
            db.query(
                day_expression.label("day"),
                func.count(DetectionTask.id).label("task_count"),
                func.coalesce(func.sum(DetectionTask.total_objects), 0).label(
                    "object_count"
                ),
                func.coalesce(func.sum(DetectionTask.total_images), 0).label(
                    "image_count"
                ),
            )
            .filter(
                DetectionTask.user_id == user_id,
                DetectionTask.created_at >= start_at,
            )
            .group_by(day_expression)
            .order_by(day_expression)
            .all()
        )

        date_map = {}
        for row in rows:
            day_value = row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
            date_map[day_value] = {
                "date": day_value,
                "task_count": int(row.task_count or 0),
                "object_count": int(row.object_count or 0),
                "image_count": int(row.image_count or 0),
            }

        trend = []
        for offset in range(days):
            day_value = (first_day + timedelta(days=offset)).isoformat()
            trend.append(
                date_map.get(
                    day_value,
                    {
                        "date": day_value,
                        "task_count": 0,
                        "object_count": 0,
                        "image_count": 0,
                    },
                )
            )
        return {"trend": trend, "period_days": days}

    @staticmethod
    def get_class_distribution(db: Session, user_id: int, days: int = 30) -> dict:
        """返回目标类别分布。"""
        start_at = datetime.now() - timedelta(days=days)
        rows = (
            db.query(
                DetectionResult.class_name,
                func.count(DetectionResult.id).label("count"),
            )
            .join(DetectionTask, DetectionResult.task_id == DetectionTask.id)
            .filter(
                DetectionTask.user_id == user_id,
                DetectionTask.created_at >= start_at,
            )
            .group_by(DetectionResult.class_name)
            .order_by(func.count(DetectionResult.id).desc())
            .all()
        )
        return {
            "distribution": [
                {"name": row.class_name, "value": int(row.count)} for row in rows
            ],
            "period_days": days,
        }

    @staticmethod
    def get_scene_distribution(db: Session, user_id: int, days: int = 30) -> dict:
        """返回检测场景任务分布。"""
        start_at = datetime.now() - timedelta(days=days)
        rows = (
            db.query(
                DetectionScene.display_name,
                func.count(DetectionTask.id).label("count"),
            )
            .join(DetectionScene, DetectionTask.scene_id == DetectionScene.id)
            .filter(
                DetectionTask.user_id == user_id,
                DetectionTask.created_at >= start_at,
            )
            .group_by(DetectionScene.display_name)
            .order_by(func.count(DetectionTask.id).desc())
            .all()
        )
        return {
            "distribution": [
                {"name": row.display_name, "value": int(row.count)} for row in rows
            ],
            "period_days": days,
        }

    @staticmethod
    def get_type_distribution(db: Session, user_id: int, days: int = 30) -> dict:
        """返回检测任务类型分布。"""
        start_at = datetime.now() - timedelta(days=days)
        rows = (
            db.query(
                DetectionTask.task_type,
                func.count(DetectionTask.id).label("count"),
            )
            .filter(
                DetectionTask.user_id == user_id,
                DetectionTask.created_at >= start_at,
            )
            .group_by(DetectionTask.task_type)
            .order_by(func.count(DetectionTask.id).desc())
            .all()
        )
        type_names = {
            "single": "单图检测",
            "batch": "批量检测",
            "zip": "ZIP 检测",
            "folder": "文件夹检测",
            "video": "视频检测",
            "camera": "摄像头检测",
        }
        return {
            "distribution": [
                {
                    "name": type_names.get(row.task_type, row.task_type),
                    "value": int(row.count),
                }
                for row in rows
            ],
            "period_days": days,
        }


dashboard_service = DashboardService()
