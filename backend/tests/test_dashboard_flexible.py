"""灵活数据看板（自定义时间段 + 缺陷类别过滤）服务与接口测试。"""

import json
from datetime import date, datetime, timedelta

from app.entity.db_models import (
    DetectionResult,
    DetectionScene,
    DetectionTask,
    Role,
    User,
    UserRole,
)
from app.services.dashboard_service import dashboard_service

from tests.test_day10 import register_and_login


def _clear_detection(db_session) -> None:
    db_session.query(DetectionResult).delete()
    db_session.query(DetectionTask).delete()
    db_session.commit()


def _ensure_user(db_session, username: str = "flex_service_user") -> User:
    """确保存在一个测试用户，服务层测试不经过注册接口。"""
    user = db_session.query(User).filter_by(username=username).first()
    if user is None:
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password="x",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _make_scene(db_session, name: str) -> DetectionScene:
    scene = DetectionScene(
        name=name,
        display_name="织物缺陷检测",
        category="industry",
        class_names=["hole", "stain"],
        class_names_cn={"hole": "破洞", "stain": "污渍"},
        is_active=False,
    )
    db_session.add(scene)
    db_session.commit()
    db_session.refresh(scene)
    return scene


def _make_task_with_defects(
    db_session,
    user_id: int,
    scene_id: int,
    created_at: datetime,
    defects: list[tuple[str, str, str]],
) -> DetectionTask:
    """创建一条指定创建时间的任务，defects 为 (class_name, class_name_cn, image_path)。"""
    task = DetectionTask(
        user_id=user_id,
        scene_id=scene_id,
        task_type="single",
        status="completed",
        total_images=len({image for _, _, image in defects}) or 1,
        total_objects=len(defects),
        total_inference_time=10.0,
        created_at=created_at,
        completed_at=created_at,
    )
    db_session.add(task)
    db_session.flush()
    for class_name, class_name_cn, image_path in defects:
        db_session.add(
            DetectionResult(
                task_id=task.id,
                image_path=image_path,
                class_name=class_name,
                class_name_cn=class_name_cn,
                class_id=0,
                confidence=0.9,
                bbox=[1, 2, 3, 4],
                created_at=created_at,
            )
        )
    db_session.commit()
    db_session.refresh(task)
    return task


class TestFlexibleDashboardService:
    """直接调用服务层，覆盖时间段窗口与缺陷过滤逻辑。"""

    def test_custom_date_range_windows_tasks(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "flex_range_scene")
        user = _ensure_user(db_session)
        # 6-10 命中，6-20 落在窗口外。
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0), [("hole", "破洞", "a.jpg")],
        )
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 20, 9, 0), [("stain", "污渍", "b.jpg")],
        )
        stats = dashboard_service.get_statistics(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 15),
        )
        assert stats["total_tasks"] == 1
        assert stats["total_objects"] == 1

    def test_defect_filter_scopes_counts(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "flex_defect_scene")
        user = _ensure_user(db_session)
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0),
            [("hole", "破洞", "a.jpg"), ("stain", "污渍", "a.jpg")],
        )
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 11, 9, 0), [("hole", "破洞", "b.jpg")],
        )
        # 仅按 hole 过滤：两条任务都命中，目标数 2。
        stats = dashboard_service.get_statistics(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
            class_names=["hole"],
        )
        assert stats["total_objects"] == 2
        assert stats["total_tasks"] == 2

    def test_defect_filter_accepts_chinese_name(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "flex_cn_scene")
        user = _ensure_user(db_session)
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0),
            [("hole", "破洞", "a.jpg"), ("stain", "污渍", "a.jpg")],
        )
        stats = dashboard_service.get_statistics(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
            class_names=["破洞"],
        )
        assert stats["total_objects"] == 1

    def test_defect_trend_zero_fills_and_splits_series(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "flex_trend_scene")
        user = _ensure_user(db_session)
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 2, 9, 0), [("hole", "破洞", "a.jpg")],
        )
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 4, 9, 0),
            [("hole", "破洞", "b.jpg"), ("stain", "污渍", "b.jpg")],
        )
        result = dashboard_service.get_defect_trend(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 5),
        )
        assert result["dates"] == [
            "2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05",
        ]
        series = {item["name"]: item for item in result["series"]}
        assert series["hole"]["data"] == [0, 1, 0, 1, 0]
        assert series["hole"]["name_cn"] == "破洞"
        assert series["stain"]["data"] == [0, 0, 0, 1, 0]

    def test_defect_options_lists_seen_classes(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "flex_options_scene")
        user = _ensure_user(db_session)
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0),
            [("hole", "破洞", "a.jpg"), ("hole", "破洞", "a.jpg"),
             ("stain", "污渍", "a.jpg")],
        )
        options = dashboard_service.get_defect_options(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
        )
        by_name = {item["name"]: item for item in options["options"]}
        assert by_name["hole"]["count"] == 2
        assert by_name["hole"]["name_cn"] == "破洞"
        assert by_name["stain"]["count"] == 1


class TestDefectNameMatching:
    """缺陷类别的中英互认与大小写不敏感匹配。"""

    def test_chinese_query_matches_english_only_records(self, db_session):
        # 老数据：class_name 为英文，class_name_cn 为空（NULL）。
        _clear_detection(db_session)
        scene = _make_scene(db_session, "match_legacy_scene")
        user = _ensure_user(db_session)
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0),
            [("hole", None, "a.jpg"), ("hole", None, "b.jpg")],
        )
        # 用中文“破洞”查询，应经场景映射解析回英文 code hole 命中。
        stats = dashboard_service.get_statistics(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
            class_names=["破洞"],
        )
        assert stats["total_objects"] == 2

    def test_case_insensitive_match(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "match_case_scene")
        user = _ensure_user(db_session)
        # 存入大小写不一致的英文名。
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0),
            [("Hole", None, "a.jpg"), ("HOLE", None, "b.jpg")],
        )
        stats = dashboard_service.get_statistics(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
            class_names=["hole"],
        )
        assert stats["total_objects"] == 2

    def test_english_query_still_matches(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "match_en_scene")
        user = _ensure_user(db_session)
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0),
            [("hole", "破洞", "a.jpg"), ("stain", "污渍", "a.jpg")],
        )
        stats = dashboard_service.get_statistics(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
            class_names=["hole"],
        )
        assert stats["total_objects"] == 1


class TestFlexibleDashboardApi:
    """接口层：自定义时间段、缺陷过滤参数与校验。"""

    def _grant_manager(self, client, db_session, username: str) -> dict[str, str]:
        headers = register_and_login(client, username)
        user = db_session.query(User).filter_by(username=username).one()
        role = db_session.query(Role).filter_by(name="production_manager").one()
        if not any(item.role_id == role.id for item in user.user_roles):
            db_session.add(UserRole(user_id=user.id, role_id=role.id))
            db_session.commit()
        return headers

    def test_defect_trend_endpoint_and_range_validation(self, client, db_session):
        _clear_detection(db_session)
        headers = self._grant_manager(client, db_session, "flex_api_user")
        scene = _make_scene(db_session, "flex_api_scene")
        user = db_session.query(User).filter_by(username="flex_api_user").one()
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime.now() - timedelta(days=1),
            [("hole", "破洞", "a.jpg")],
        )

        ok = client.get(
            "/api/dashboard/defect-trend?days=7&class_name=hole", headers=headers
        )
        assert ok.status_code == 200
        assert ok.json()["series"][0]["name"] == "hole"

        options = client.get("/api/dashboard/defect-options?days=7", headers=headers)
        assert options.status_code == 200
        assert any(item["name"] == "hole" for item in options.json()["options"])

        bad = client.get(
            "/api/dashboard/statistics?start_date=2026-06-30&end_date=2026-06-01",
            headers=headers,
        )
        assert bad.status_code == 400


class TestChatToolDefectHints:
    """对话分析工具：缺陷 0 命中时返回可选类别，避免误判“没有”。"""

    def test_statistics_tool_returns_available_defects_on_zero_match(
        self, db_session, monkeypatch
    ):
        import app.agent.detection_agent as agent_module
        from sqlalchemy.orm import sessionmaker

        _clear_detection(db_session)
        scene = _make_scene(db_session, "chat_hint_scene")
        user = _ensure_user(db_session, "chat_hint_user")
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0), [("hole", "破洞", "a.jpg")],
        )

        # 工具内部用 SessionLocal() 开新会话，指向测试库；并放行权限。
        factory = sessionmaker(
            autocommit=False, autoflush=False, bind=db_session.get_bind()
        )
        monkeypatch.setattr(agent_module, "SessionLocal", factory)
        monkeypatch.setattr(
            agent_module, "_tool_permission_error", lambda _permission: None
        )

        raw = agent_module.query_detection_statistics.invoke(
            {
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "defect": "不存在的缺陷",
            }
        )
        payload = json.loads(raw)
        assert payload["defect_count"] == 0
        names = {item["name"] for item in payload["available_defects"]}
        assert "hole" in names


class TestToolDateParsing:
    """工具日期解析：仅月日按服务端当前年份补齐，杜绝 LLM 猜错年份。"""

    def test_parse_month_day_fills_current_year(self):
        from datetime import datetime as _dt

        from app.agent.detection_agent import _parse_tool_date

        today = _dt.now().date()
        # 取一个不晚于今天的月日，确保落在当年。
        assert _parse_tool_date("01-05") == date(today.year, 1, 5)
        assert _parse_tool_date("1/5") == date(today.year, 1, 5)

    def test_parse_future_month_day_rolls_back_one_year(self):
        from datetime import datetime as _dt

        from app.agent.detection_agent import _parse_tool_date

        today = _dt.now().date()
        # 12-31 相对 7 月今天属于未来 → 归上一年。
        parsed = _parse_tool_date("12-31")
        assert parsed == date(today.year - 1, 12, 31)

    def test_parse_full_date_preserved(self):
        from app.agent.detection_agent import _parse_tool_date

        assert _parse_tool_date("2025-07-15") == date(2025, 7, 15)
        assert _parse_tool_date("") is None
        assert _parse_tool_date("abc") is None

    def test_year_less_range_hits_real_data(self, db_session, monkeypatch):
        """回归用户报障：只给月日的区间应命中当年数据，而不是 0。"""
        import app.agent.detection_agent as agent_module
        from datetime import datetime as _dt
        from sqlalchemy.orm import sessionmaker

        _clear_detection(db_session)
        scene = DetectionScene(
            name="yearless_scene",
            display_name="遥感目标检测",
            category="remote_sensing",
            class_names=["aircraft"],
            class_names_cn={"aircraft": "飞机"},
            is_active=False,
        )
        db_session.add(scene)
        db_session.commit()
        db_session.refresh(scene)
        user = _ensure_user(db_session, "yearless_user")
        today = _dt.now().date()
        # 在当年、不晚于今天的一天写入 aircraft 数据。
        seeded_day = _dt(today.year, 1, 6, 9, 0)
        _make_task_with_defects(
            db_session, user.id, scene.id, seeded_day,
            [("aircraft", "飞机", "a.jpg"), ("aircraft", "飞机", "a.jpg")],
        )

        factory = sessionmaker(
            autocommit=False, autoflush=False, bind=db_session.get_bind()
        )
        monkeypatch.setattr(agent_module, "SessionLocal", factory)
        monkeypatch.setattr(
            agent_module, "_tool_permission_error", lambda _permission: None
        )

        raw = agent_module.query_detection_statistics.invoke(
            {"start_date": "01-05", "end_date": "01-07", "defect": "飞机"}
        )
        payload = json.loads(raw)
        assert payload["defect_count"] == 2
