"""灵活数据看板（自定义时间段 + 缺陷类别过滤）服务与接口测试。"""

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
