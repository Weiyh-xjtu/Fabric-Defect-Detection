"""数据看板聚合统计服务。"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Query, Session

from app.entity.db_models import DetectionResult, DetectionScene, DetectionTask


class DashboardService:
    """按指定用户或全厂范围聚合检测任务数据。"""

    @staticmethod
    def _calc_growth(current: float, previous: float) -> float:
        """计算当前周期相对上一周期的增长率。"""
        if previous == 0:
            return 100.0 if current > 0 else 0.0
        return round((current - previous) / previous * 100, 1)

    @staticmethod
    def _resolve_window(
        start_date: date | None = None,
        end_date: date | None = None,
        days: int = 30,
    ) -> tuple[datetime, datetime]:
        """解析统计时间窗口，返回左闭右开的 (start_at, end_at)。

        显式日期优先：给定 start_date / end_date 时按自然日边界取整；
        否则回退到「最近 days 天到当前时刻」的滚动窗口。
        """
        if start_date or end_date:
            end_day = end_date or date.today()
            # 无起始日期时，用 end_date 往前推 days 天，保持窗口长度一致。
            start_day = start_date or (end_day - timedelta(days=days - 1))
            start_at = datetime.combine(start_day, time.min)
            # 右开区间：结束日期当天 23:59:59 也要包含在内。
            end_at = datetime.combine(end_day + timedelta(days=1), time.min)
            return start_at, end_at
        now = datetime.now()
        return now - timedelta(days=days), now

    @classmethod
    def _resolve_day_window(
        cls,
        start_date: date | None = None,
        end_date: date | None = None,
        days: int = 30,
    ) -> tuple[datetime, datetime]:
        """按自然日对齐的时间窗口，供每日趋势分桶使用。

        与 `_resolve_window` 的区别：滚动模式下也对齐到自然日边界，
        窗口为「今天往前 days-1 天的 00:00」到「明天 00:00」，
        保证恰好 days 个自然日桶且包含今天。
        """
        if start_date or end_date:
            return cls._resolve_window(start_date, end_date, days)
        end_day = date.today()
        start_day = end_day - timedelta(days=days - 1)
        return (
            datetime.combine(start_day, time.min),
            datetime.combine(end_day + timedelta(days=1), time.min),
        )

    @staticmethod
    def _normalize_classes(class_names: list[str] | None) -> list[str] | None:
        """清洗缺陷类别过滤参数，去空白、去重且保持稳定。"""
        if not class_names:
            return None
        cleaned = [name.strip() for name in class_names if name and name.strip()]
        # 去重同时保留首次出现顺序，便于返回结果稳定。
        seen: dict[str, None] = {}
        for name in cleaned:
            seen.setdefault(name, None)
        return list(seen) or None

    @staticmethod
    def _scene_alias_map(
        db: Session, scene_id: int | None = None
    ) -> dict[str, set[str]]:
        """构建「小写别名 → 英文类别 code 集合」映射，用于中英/大小写归一。

        数据来源是所有场景的 class_names / class_names_cn：
          - 英文 code 本身（小写）指向自己；
          - 中文名（小写化，中文无大小写影响，仅为统一 key）指向对应英文 code。
        这样用户用中文、英文或不同大小写都能解析回英文 code 精确匹配。
        指定 scene_id 时只用该场景的登记映射，避免跨场景同名词互相扩散。
        """
        alias_map: dict[str, set[str]] = {}
        scene_query = db.query(DetectionScene)
        if scene_id is not None:
            scene_query = scene_query.filter(DetectionScene.id == scene_id)
        scenes = scene_query.all()
        for scene in scenes:
            class_names = scene.class_names if isinstance(scene.class_names, list) else []
            for code in class_names:
                if code:
                    alias_map.setdefault(str(code).strip().lower(), set()).add(str(code))
            cn_map = scene.class_names_cn if isinstance(scene.class_names_cn, dict) else {}
            for code, cn in cn_map.items():
                if code and cn:
                    alias_map.setdefault(str(cn).strip().lower(), set()).add(str(code))
                    # 英文名同样登记，保证 code→code。
                    alias_map.setdefault(str(code).strip().lower(), set()).add(str(code))
        return alias_map

    @staticmethod
    def _scene_cn_map(db: Session, scene_id: int | None = None) -> dict[str, str]:
        """场景的英文 code→中文名实时映射。

        展示时优先于 detection_results 行内的历史快照，
        保证改场景表中文名后看板立即跟随，无需刷存量数据。
        不指定 scene_id 时聚合全部场景，同一 code 由最近更新的场景生效；
        指定后只取该场景的映射，展示与筛选范围保持一致。
        """
        scene_query = db.query(DetectionScene)
        if scene_id is not None:
            scene_query = scene_query.filter(DetectionScene.id == scene_id)
        scenes = scene_query.all()
        scenes.sort(key=lambda s: s.updated_at or datetime.min)
        mapping: dict[str, str] = {}
        for scene in scenes:
            cn_map = scene.class_names_cn if isinstance(scene.class_names_cn, dict) else {}
            for code, name in cn_map.items():
                if code and name:
                    # 按 updated_at 升序遍历，后更新的场景覆盖先前的。
                    mapping[str(code)] = str(name)
        return mapping

    @classmethod
    def _resolve_class_terms(
        cls,
        db: Session,
        class_names: list[str] | None,
        scene_id: int | None = None,
    ) -> tuple[set[str], list[str]] | None:
        """把用户输入的类别词解析成 (英文 code 小写集合, 原始词列表)。

        返回 None 表示无过滤。英文 code 集合用于对 class_name 做大小写不敏感匹配；
        原始词列表用于对 class_name_cn 做直接匹配（兜底老数据/未登记映射的场景）。
        """
        normalized = cls._normalize_classes(class_names)
        if not normalized:
            return None
        alias_map = cls._scene_alias_map(db, scene_id)
        code_terms: set[str] = set()
        for term in normalized:
            key = term.lower()
            if key in alias_map:
                code_terms.update(code.lower() for code in alias_map[key])
            else:
                # 未登记的词也按原样纳入，保证精确写法仍可命中。
                code_terms.add(key)
        return code_terms, normalized

    @classmethod
    def _apply_class_filter(
        cls,
        db: Session,
        query: "Query",
        class_names: list[str] | None,
        scene_id: int | None = None,
    ) -> "Query":
        """按缺陷类别过滤，支持中英互认与大小写不敏感。

        匹配任一条件即命中：
          - class_name 小写化后落在解析出的英文 code 集合里；
          - class_name_cn 精确等于用户原始输入（兜底未建立映射的历史数据）。
        """
        resolved = cls._resolve_class_terms(db, class_names, scene_id)
        if resolved is None:
            return query
        code_terms, raw_terms = resolved
        return query.filter(
            (func.lower(DetectionResult.class_name).in_(code_terms))
            | (DetectionResult.class_name_cn.in_(raw_terms))
        )

    @staticmethod
    def _apply_scene_filter(query: "Query", scene_id: int | None) -> "Query":
        """按检测场景过滤任务，None 表示不隔离（全部场景）。"""
        if scene_id is None:
            return query
        return query.filter(DetectionTask.scene_id == scene_id)

    def _statistics_for_period(
        self,
        db: Session,
        user_id: int | None,
        start_at: datetime,
        end_at: datetime,
        class_names: list[str] | None = None,
        scene_id: int | None = None,
    ) -> dict:
        """查询一个左闭右开时间段内的汇总数据。

        未指定缺陷类别时直接聚合 DetectionTask 上的冗余统计列；
        指定缺陷类别时改为在 DetectionResult 上聚合目标级数据，
        图片数按任务去重计数，任务数按命中缺陷的任务去重计数。
        """
        normalized = self._normalize_classes(class_names)
        if not normalized:
            row = (
                db.query(
                    func.count(DetectionTask.id).label("total_tasks"),
                    func.coalesce(func.sum(DetectionTask.total_images), 0).label(
                        "total_images"
                    ),
                    func.coalesce(func.sum(DetectionTask.total_objects), 0).label(
                        "total_objects"
                    ),
                    func.coalesce(
                        func.avg(DetectionTask.total_inference_time), 0
                    ).label("avg_inference_time"),
                )
                .filter(
                    DetectionTask.created_at >= start_at,
                    DetectionTask.created_at < end_at,
                )
            )
            row = self._apply_scene_filter(row, scene_id)
            if user_id is not None:
                row = row.filter(DetectionTask.user_id == user_id)
            result = row.one()
            return {
                "total_tasks": int(result.total_tasks or 0),
                "total_images": int(result.total_images or 0),
                "total_objects": int(result.total_objects or 0),
                "avg_inference_time": float(result.avg_inference_time or 0),
            }

        # 缺陷级聚合：以检测结果为主体，join 回任务表做时间/用户过滤。
        query = (
            db.query(
                func.count(func.distinct(DetectionTask.id)).label("total_tasks"),
                func.count(func.distinct(DetectionResult.image_path)).label(
                    "total_images"
                ),
                func.count(DetectionResult.id).label("total_objects"),
            )
            .join(DetectionTask, DetectionResult.task_id == DetectionTask.id)
            .filter(
                DetectionTask.created_at >= start_at,
                DetectionTask.created_at < end_at,
            )
        )
        query = self._apply_scene_filter(query, scene_id)
        if user_id is not None:
            query = query.filter(DetectionTask.user_id == user_id)
        query = self._apply_class_filter(db, query, normalized, scene_id)
        result = query.one()
        return {
            "total_tasks": int(result.total_tasks or 0),
            "total_images": int(result.total_images or 0),
            "total_objects": int(result.total_objects or 0),
            # 缺陷级视角下平均推理耗时按目标级样本无意义，置 0 由前端隐藏。
            "avg_inference_time": 0.0,
        }

    def get_statistics(
        self,
        db: Session,
        user_id: int | None,
        days: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
        class_names: list[str] | None = None,
        scene_id: int | None = None,
    ) -> dict:
        """返回任务、图片、目标、平均耗时及环比增长率。

        支持自定义时间段（start_date/end_date）、缺陷类别过滤（class_names）
        与检测场景隔离（scene_id）。环比对比的是等长的前一个时间窗口。
        """
        start_at, end_at = self._resolve_window(start_date, end_date, days)
        span = end_at - start_at
        previous_start = start_at - span
        current = self._statistics_for_period(
            db, user_id, start_at, end_at, class_names, scene_id
        )
        previous = self._statistics_for_period(
            db, user_id, previous_start, start_at, class_names, scene_id
        )

        return {
            "total_tasks": current["total_tasks"],
            "total_images": current["total_images"],
            "total_objects": current["total_objects"],
            "avg_inference_time": round(current["avg_inference_time"], 2),
            "growth": {
                "tasks": self._calc_growth(
                    current["total_tasks"], previous["total_tasks"]
                ),
                "images": self._calc_growth(
                    current["total_images"], previous["total_images"]
                ),
                "objects": self._calc_growth(
                    current["total_objects"], previous["total_objects"]
                ),
                "inference_time": self._calc_growth(
                    current["avg_inference_time"], previous["avg_inference_time"]
                ),
            },
            "period_days": days,
            "start_at": start_at.isoformat(timespec="seconds"),
            "end_at": end_at.isoformat(timespec="seconds"),
            "class_names": self._normalize_classes(class_names) or [],
            "scene_id": scene_id,
        }

    def get_trend(
        self,
        db: Session,
        user_id: int | None,
        days: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
        class_names: list[str] | None = None,
        scene_id: int | None = None,
    ) -> dict:
        """返回包含空白日期补零的每日检测趋势。

        未过滤缺陷时按任务聚合；过滤缺陷时按目标级聚合（object_count 为
        命中目标数，image_count 为去重图片数，task_count 为去重任务数）。
        """
        start_at, end_at = self._resolve_day_window(start_date, end_date, days)
        first_day = start_at.date()
        # 桶数量按窗口天数计算（右开区间，减 1 天得到最后一个自然日）。
        bucket_days = max(1, (end_at.date() - first_day).days)

        # func.date 在 PostgreSQL 返回 date、在 SQLite 返回 ISO 字符串，
        # 两端都不会触发 SQLAlchemy Date 类型处理器的兼容问题。
        day_expression = func.date(DetectionTask.created_at)
        normalized = self._normalize_classes(class_names)
        if not normalized:
            query = (
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
                    DetectionTask.created_at >= start_at,
                    DetectionTask.created_at < end_at,
                )
            )
        else:
            query = (
                db.query(
                    day_expression.label("day"),
                    func.count(func.distinct(DetectionTask.id)).label("task_count"),
                    func.count(DetectionResult.id).label("object_count"),
                    func.count(func.distinct(DetectionResult.image_path)).label(
                        "image_count"
                    ),
                )
                .join(DetectionTask, DetectionResult.task_id == DetectionTask.id)
                .filter(
                    DetectionTask.created_at >= start_at,
                    DetectionTask.created_at < end_at,
                )
            )
            query = self._apply_class_filter(db, query, normalized, scene_id)
        query = self._apply_scene_filter(query, scene_id)
        if user_id is not None:
            query = query.filter(DetectionTask.user_id == user_id)
        rows = query.group_by(day_expression).order_by(day_expression).all()

        date_map = {}
        for row in rows:
            day_value = (
                row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
            )
            date_map[day_value] = {
                "date": day_value,
                "task_count": int(row.task_count or 0),
                "object_count": int(row.object_count or 0),
                "image_count": int(row.image_count or 0),
            }

        trend = []
        for offset in range(bucket_days):
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
        return {
            "trend": trend,
            "period_days": days,
            "start_at": start_at.isoformat(timespec="seconds"),
            "end_at": end_at.isoformat(timespec="seconds"),
            "class_names": normalized or [],
            "scene_id": scene_id,
        }

    def get_defect_trend(
        self,
        db: Session,
        user_id: int | None,
        days: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
        class_names: list[str] | None = None,
        top_n: int = 8,
        scene_id: int | None = None,
    ) -> dict:
        """返回按缺陷类别拆分的每日趋势，用于多折线对比图。

        未指定 class_names 时自动取窗口内目标数最多的 top_n 个缺陷类别。
        返回结构：{ dates, series:[{name, name_cn, data:[...]}] }，
        每条 series 的 data 与 dates 等长且已补零。
        """
        start_at, end_at = self._resolve_day_window(start_date, end_date, days)
        first_day = start_at.date()
        bucket_days = max(1, (end_at.date() - first_day).days)
        normalized = self._normalize_classes(class_names)

        base_filters = [
            DetectionTask.created_at >= start_at,
            DetectionTask.created_at < end_at,
        ]

        # 决定要展示哪些缺陷类别。
        if normalized:
            selected_classes = normalized
        else:
            top_query = (
                db.query(
                    DetectionResult.class_name,
                    func.count(DetectionResult.id).label("count"),
                )
                .join(DetectionTask, DetectionResult.task_id == DetectionTask.id)
                .filter(*base_filters)
            )
            top_query = self._apply_scene_filter(top_query, scene_id)
            if user_id is not None:
                top_query = top_query.filter(DetectionTask.user_id == user_id)
            top_rows = (
                top_query.group_by(DetectionResult.class_name)
                .order_by(func.count(DetectionResult.id).desc())
                .limit(max(1, top_n))
                .all()
            )
            selected_classes = [row.class_name for row in top_rows]

        dates = [
            (first_day + timedelta(days=offset)).isoformat()
            for offset in range(bucket_days)
        ]
        if not selected_classes:
            return {
                "dates": dates,
                "series": [],
                "period_days": days,
                "start_at": start_at.isoformat(timespec="seconds"),
                "end_at": end_at.isoformat(timespec="seconds"),
            }

        day_expression = func.date(DetectionTask.created_at)
        query = (
            db.query(
                day_expression.label("day"),
                DetectionResult.class_name.label("class_name"),
                func.count(DetectionResult.id).label("count"),
            )
            .join(DetectionTask, DetectionResult.task_id == DetectionTask.id)
            .filter(*base_filters)
        )
        query = self._apply_class_filter(db, query, selected_classes, scene_id)
        query = self._apply_scene_filter(query, scene_id)
        if user_id is not None:
            query = query.filter(DetectionTask.user_id == user_id)
        rows = (
            query.group_by(day_expression, DetectionResult.class_name)
            .order_by(day_expression)
            .all()
        )

        # class_name -> {date -> count}
        counts: dict[str, dict[str, int]] = {name: {} for name in selected_classes}
        for row in rows:
            day_value = (
                row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
            )
            counts.setdefault(row.class_name, {})[day_value] = int(row.count or 0)

        cn_map = self._class_name_cn_map(db, selected_classes, scene_id)
        series = []
        for name in selected_classes:
            day_counts = counts.get(name, {})
            series.append(
                {
                    "name": name,
                    "name_cn": cn_map.get(name, name),
                    "data": [day_counts.get(day, 0) for day in dates],
                    "total": sum(day_counts.values()),
                }
            )
        return {
            "dates": dates,
            "series": series,
            "period_days": days,
            "start_at": start_at.isoformat(timespec="seconds"),
            "end_at": end_at.isoformat(timespec="seconds"),
            "scene_id": scene_id,
        }

    @classmethod
    def _class_name_cn_map(
        cls,
        db: Session,
        class_names: list[str],
        scene_id: int | None = None,
    ) -> dict[str, str]:
        """查询缺陷类别对应的中文名映射。

        优先取场景表的实时映射（改名立即生效），
        场景未登记的类别再回退到历史结果行里的快照。
        """
        if not class_names:
            return {}
        mapping = {
            code: cn
            for code, cn in cls._scene_cn_map(db, scene_id).items()
            if code in class_names
        }
        missing = [name for name in class_names if name not in mapping]
        if not missing:
            return mapping
        rows = (
            db.query(DetectionResult.class_name, DetectionResult.class_name_cn)
            .filter(DetectionResult.class_name.in_(missing))
            .filter(DetectionResult.class_name_cn.isnot(None))
            .distinct()
            .all()
        )
        for class_name, class_name_cn in rows:
            if class_name_cn:
                mapping.setdefault(class_name, class_name_cn)
        return mapping

    def get_defect_options(
        self,
        db: Session,
        user_id: int | None,
        days: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
        scene_id: int | None = None,
    ) -> dict:
        """返回时间窗口内实际出现过的缺陷类别，用于前端下拉筛选。"""
        start_at, end_at = self._resolve_window(start_date, end_date, days)
        query = (
            db.query(
                DetectionResult.class_name,
                func.max(DetectionResult.class_name_cn).label("class_name_cn"),
                func.count(DetectionResult.id).label("count"),
            )
            .join(DetectionTask, DetectionResult.task_id == DetectionTask.id)
            .filter(
                DetectionTask.created_at >= start_at,
                DetectionTask.created_at < end_at,
            )
        )
        query = self._apply_scene_filter(query, scene_id)
        if user_id is not None:
            query = query.filter(DetectionTask.user_id == user_id)
        rows = (
            query.group_by(DetectionResult.class_name)
            .order_by(func.count(DetectionResult.id).desc())
            .all()
        )
        # 场景表实时映射优先，历史行内快照兜底。
        scene_cn = self._scene_cn_map(db, scene_id)
        return {
            "options": [
                {
                    "name": row.class_name,
                    "name_cn": scene_cn.get(row.class_name)
                    or row.class_name_cn
                    or row.class_name,
                    "count": int(row.count or 0),
                }
                for row in rows
            ]
        }

    def get_class_distribution(
        self,
        db: Session,
        user_id: int | None,
        days: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
        class_names: list[str] | None = None,
        scene_id: int | None = None,
    ) -> dict:
        """返回目标类别分布。"""
        start_at, end_at = self._resolve_window(start_date, end_date, days)
        query = (
            db.query(
                DetectionResult.class_name,
                func.max(DetectionResult.class_name_cn).label("class_name_cn"),
                func.count(DetectionResult.id).label("count"),
            )
            .join(DetectionTask, DetectionResult.task_id == DetectionTask.id)
            .filter(
                DetectionTask.created_at >= start_at,
                DetectionTask.created_at < end_at,
            )
        )
        query = self._apply_class_filter(db, query, class_names, scene_id)
        query = self._apply_scene_filter(query, scene_id)
        if user_id is not None:
            query = query.filter(DetectionTask.user_id == user_id)
        rows = (
            query.group_by(DetectionResult.class_name)
            .order_by(func.count(DetectionResult.id).desc())
            .all()
        )
        # 场景表实时映射优先，历史行内快照兜底。
        scene_cn = self._scene_cn_map(db, scene_id)
        return {
            "distribution": [
                {
                    "name": row.class_name,
                    "name_cn": scene_cn.get(row.class_name)
                    or row.class_name_cn
                    or row.class_name,
                    "value": int(row.count),
                }
                for row in rows
            ],
            "period_days": days,
            "start_at": start_at.isoformat(timespec="seconds"),
            "end_at": end_at.isoformat(timespec="seconds"),
        }

    def get_scene_distribution(
        self,
        db: Session,
        user_id: int | None,
        days: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
        scene_id: int | None = None,
    ) -> dict:
        """返回检测场景任务分布。

        指定 scene_id 时只统计该场景（用于校验隔离视图，图上只会有一条）。
        """
        start_at, end_at = self._resolve_window(start_date, end_date, days)
        query = (
            db.query(
                DetectionScene.display_name,
                func.count(DetectionTask.id).label("count"),
            )
            .join(DetectionScene, DetectionTask.scene_id == DetectionScene.id)
            .filter(
                DetectionTask.created_at >= start_at,
                DetectionTask.created_at < end_at,
            )
        )
        query = self._apply_scene_filter(query, scene_id)
        if user_id is not None:
            query = query.filter(DetectionTask.user_id == user_id)
        rows = (
            query.group_by(DetectionScene.display_name)
            .order_by(func.count(DetectionTask.id).desc())
            .all()
        )
        return {
            "distribution": [
                {"name": row.display_name, "value": int(row.count)} for row in rows
            ],
            "period_days": days,
            "start_at": start_at.isoformat(timespec="seconds"),
            "end_at": end_at.isoformat(timespec="seconds"),
        }

    def get_type_distribution(
        self,
        db: Session,
        user_id: int | None,
        days: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
        scene_id: int | None = None,
    ) -> dict:
        """返回检测任务类型分布。"""
        start_at, end_at = self._resolve_window(start_date, end_date, days)
        query = (
            db.query(
                DetectionTask.task_type,
                func.count(DetectionTask.id).label("count"),
            )
            .filter(
                DetectionTask.created_at >= start_at,
                DetectionTask.created_at < end_at,
            )
        )
        query = self._apply_scene_filter(query, scene_id)
        if user_id is not None:
            query = query.filter(DetectionTask.user_id == user_id)
        rows = (
            query.group_by(DetectionTask.task_type)
            .order_by(func.count(DetectionTask.id).desc())
            .all()
        )
        type_names = {
            "single": "单图检测",
            "batch": "批量检测",
            "zip": "批量检测",
            "folder": "批量检测",
            "video": "视频检测",
            "camera": "摄像头检测",
        }
        merged_counts: dict[str, int] = {}
        for row in rows:
            display_name = type_names.get(row.task_type, row.task_type)
            merged_counts[display_name] = (
                merged_counts.get(display_name, 0) + int(row.count)
            )
        return {
            "distribution": [
                {"name": name, "value": value}
                for name, value in merged_counts.items()
            ],
            "period_days": days,
            "start_at": start_at.isoformat(timespec="seconds"),
            "end_at": end_at.isoformat(timespec="seconds"),
        }

    def get_scene_options(self, db: Session) -> dict:
        """返回启用的检测场景，用于看板场景筛选下拉。"""
        scenes = (
            db.query(DetectionScene)
            .filter(DetectionScene.is_active.is_(True))
            .order_by(DetectionScene.id.asc())
            .all()
        )
        return {
            "options": [
                {
                    "id": scene.id,
                    "name": scene.name,
                    "display_name": scene.display_name,
                }
                for scene in scenes
            ]
        }


dashboard_service = DashboardService()
