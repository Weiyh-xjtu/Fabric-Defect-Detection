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
from app.services.history_service import history_service

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


class TestSceneCnNameFollowsSceneTable:
    """展示用中文名实时读场景表：改 class_names_cn 后无需刷存量数据即生效。"""

    def _rename_hole(self, db_session, scene, new_name: str) -> None:
        scene.class_names_cn = {**scene.class_names_cn, "hole": new_name}
        db_session.add(scene)
        db_session.commit()

    def test_class_distribution_prefers_scene_mapping(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "follow_dist_scene")
        user = _ensure_user(db_session)
        # 行内快照仍是旧名「破洞」。
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0),
            [("hole", "破洞", "a.jpg"), ("stain", "污渍", "a.jpg")],
        )
        self._rename_hole(db_session, scene, "织物破洞")

        dist = dashboard_service.get_class_distribution(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
        )
        by_name = {item["name"]: item["name_cn"] for item in dist["distribution"]}
        assert by_name["hole"] == "织物破洞"
        # 未改名的类别不受影响。
        assert by_name["stain"] == "污渍"

    def test_defect_options_prefer_scene_mapping(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "follow_options_scene")
        user = _ensure_user(db_session)
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0), [("hole", "破洞", "a.jpg")],
        )
        self._rename_hole(db_session, scene, "织物破洞")

        options = dashboard_service.get_defect_options(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
        )
        by_name = {item["name"]: item["name_cn"] for item in options["options"]}
        assert by_name["hole"] == "织物破洞"

    def test_defect_trend_prefers_scene_mapping(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "follow_trend_scene")
        user = _ensure_user(db_session)
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 2, 9, 0), [("hole", "破洞", "a.jpg")],
        )
        self._rename_hole(db_session, scene, "织物破洞")

        result = dashboard_service.get_defect_trend(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 5),
        )
        series = {item["name"]: item for item in result["series"]}
        assert series["hole"]["name_cn"] == "织物破洞"

    def test_trend_falls_back_to_snapshot_when_scene_unmapped(self, db_session):
        # 场景表没登记的类别，仍用历史行内快照兜底。
        _clear_detection(db_session)
        scene = _make_scene(db_session, "follow_fallback_scene")
        user = _ensure_user(db_session)
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 2, 9, 0), [("scratch", "划痕", "a.jpg")],
        )
        result = dashboard_service.get_defect_trend(
            db_session, None,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 5),
        )
        series = {item["name"]: item for item in result["series"]}
        assert series["scratch"]["name_cn"] == "划痕"

    def test_history_detail_prefers_scene_mapping(self, db_session):
        _clear_detection(db_session)
        scene = _make_scene(db_session, "follow_history_scene")
        user = _ensure_user(db_session)
        task = _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime(2026, 6, 10, 9, 0),
            [("hole", "破洞", "a.jpg"), ("scratch", "划痕", "a.jpg")],
        )
        self._rename_hole(db_session, scene, "织物破洞")

        detail = history_service.get_task_detail(db_session, user.id, task.id)
        assert detail is not None
        # 改名类别跟随场景表，未登记类别（scratch）用行内快照兜底。
        assert detail["class_counts"] == {"织物破洞": 1, "划痕": 1}
        by_name = {
            item["class_name"]: item["class_name_cn"] for item in detail["results"]
        }
        assert by_name["hole"] == "织物破洞"
        assert by_name["scratch"] == "划痕"


class TestSceneIsolation:
    """场景筛选隔离：scene_id 生效于全部聚合出口，且不影响全场景视图。"""

    def _make_two_scenes(self, db_session):
        """场景 A（织物）与场景 B（遥感），B 的 hole 同名但中文不同。

        场景 name 全局唯一且各用例共享测试库，存在即复用。
        """
        user = _ensure_user(db_session, "scene_iso_user")
        scene_a = (
            db_session.query(DetectionScene).filter_by(name="iso_scene_a").first()
            or _make_scene(db_session, "iso_scene_a")
        )
        scene_b = db_session.query(DetectionScene).filter_by(name="iso_scene_b").first()
        if scene_b is None:
            scene_b = DetectionScene(
                name="iso_scene_b",
                display_name="遥感目标检测",
                category="remote_sensing",
                class_names=["hole", "aircraft"],
                class_names_cn={"hole": "孔洞", "aircraft": "飞机"},
                is_active=False,
            )
            db_session.add(scene_b)
            db_session.commit()
            db_session.refresh(scene_b)
        # 场景 A：hole×2、stain×1；场景 B：hole×1、aircraft×1。
        _make_task_with_defects(
            db_session, user.id, scene_a.id,
            datetime(2026, 6, 10, 9, 0),
            [("hole", "破洞", "a1.jpg"), ("hole", "破洞", "a1.jpg"),
             ("stain", "污渍", "a2.jpg")],
        )
        _make_task_with_defects(
            db_session, user.id, scene_b.id,
            datetime(2026, 6, 11, 9, 0),
            [("hole", "孔洞", "b1.jpg"), ("aircraft", "飞机", "b1.jpg")],
        )
        return user, scene_a, scene_b

    def test_statistics_isolated_by_scene(self, db_session):
        _clear_detection(db_session)
        _, scene_a, scene_b = self._make_two_scenes(db_session)
        window = dict(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))

        all_stats = dashboard_service.get_statistics(db_session, None, **window)
        a_stats = dashboard_service.get_statistics(
            db_session, None, scene_id=scene_a.id, **window
        )
        b_stats = dashboard_service.get_statistics(
            db_session, None, scene_id=scene_b.id, **window
        )
        assert all_stats["total_tasks"] == 2
        assert a_stats["total_tasks"] == 1
        assert a_stats["total_objects"] == 3
        assert b_stats["total_objects"] == 2
        assert a_stats["scene_id"] == scene_a.id

    def test_statistics_with_class_filter_isolated(self, db_session):
        # 同名 hole 只统计所选场景内的，避免跨场景混淆。
        _clear_detection(db_session)
        _, scene_a, scene_b = self._make_two_scenes(db_session)
        window = dict(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))

        a_hole = dashboard_service.get_statistics(
            db_session, None, class_names=["hole"], scene_id=scene_a.id, **window
        )
        b_hole = dashboard_service.get_statistics(
            db_session, None, class_names=["hole"], scene_id=scene_b.id, **window
        )
        assert a_hole["total_objects"] == 2
        assert b_hole["total_objects"] == 1

    def test_chinese_filter_resolves_within_scene(self, db_session):
        # 中文词解析仅用所选场景的映射：场景 B 里「孔洞」命中 hole。
        _clear_detection(db_session)
        _, scene_a, scene_b = self._make_two_scenes(db_session)
        window = dict(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))

        b_via_cn = dashboard_service.get_statistics(
            db_session, None, class_names=["孔洞"], scene_id=scene_b.id, **window
        )
        assert b_via_cn["total_objects"] == 1
        # 场景 A 没登记「孔洞」，即使历史行有同词快照也不属于该场景。
        a_via_cn = dashboard_service.get_statistics(
            db_session, None, class_names=["孔洞"], scene_id=scene_a.id, **window
        )
        assert a_via_cn["total_objects"] == 0

    def test_class_distribution_and_options_isolated(self, db_session):
        _clear_detection(db_session)
        _, scene_a, scene_b = self._make_two_scenes(db_session)
        window = dict(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))

        a_dist = dashboard_service.get_class_distribution(
            db_session, None, scene_id=scene_a.id, **window
        )
        a_names = {item["name"]: item for item in a_dist["distribution"]}
        assert set(a_names) == {"hole", "stain"}
        assert a_names["hole"]["value"] == 2
        # 中文名取所选场景的映射：A 场景 hole → 破洞。
        assert a_names["hole"]["name_cn"] == "破洞"

        b_options = dashboard_service.get_defect_options(
            db_session, None, scene_id=scene_b.id, **window
        )
        b_names = {item["name"]: item for item in b_options["options"]}
        assert set(b_names) == {"hole", "aircraft"}
        # B 场景 hole → 孔洞，不受 A 场景映射影响。
        assert b_names["hole"]["name_cn"] == "孔洞"

    def test_trend_and_defect_trend_isolated(self, db_session):
        _clear_detection(db_session)
        _, scene_a, scene_b = self._make_two_scenes(db_session)
        window = dict(start_date=date(2026, 6, 10), end_date=date(2026, 6, 11))

        a_trend = dashboard_service.get_trend(
            db_session, None, scene_id=scene_a.id, **window
        )
        # 6-10 场景 A 有 1 个任务，6-11（场景 B 的任务日）应为 0。
        by_date = {item["date"]: item for item in a_trend["trend"]}
        assert by_date["2026-06-10"]["task_count"] == 1
        assert by_date["2026-06-11"]["task_count"] == 0

        b_defect_trend = dashboard_service.get_defect_trend(
            db_session, None, scene_id=scene_b.id, **window
        )
        names = {item["name"] for item in b_defect_trend["series"]}
        assert names == {"hole", "aircraft"}
        by_name = {item["name"]: item for item in b_defect_trend["series"]}
        assert by_name["hole"]["total"] == 1
        assert by_name["hole"]["name_cn"] == "孔洞"

    def test_scene_and_type_distribution_isolated(self, db_session):
        _clear_detection(db_session)
        _, scene_a, _scene_b = self._make_two_scenes(db_session)
        window = dict(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))

        scene_dist = dashboard_service.get_scene_distribution(
            db_session, None, scene_id=scene_a.id, **window
        )
        assert scene_dist["distribution"] == [{"name": "织物缺陷检测", "value": 1}]

        type_dist = dashboard_service.get_type_distribution(
            db_session, None, scene_id=scene_a.id, **window
        )
        assert type_dist["distribution"] == [{"name": "单图检测", "value": 1}]

    def test_scene_options_lists_active_scenes(self, db_session):
        # get_scene_options 只返回启用场景。
        scene = DetectionScene(
            name="iso_active_scene",
            display_name="隔离测试启用场景",
            category="industry",
            class_names=["hole"],
            is_active=True,
        )
        db_session.add(scene)
        db_session.commit()
        try:
            options = dashboard_service.get_scene_options(db_session)
            by_id = {item["id"]: item for item in options["options"]}
            assert scene.id in by_id
            assert by_id[scene.id]["display_name"] == "隔离测试启用场景"
            # 停用场景（_make_scene 创建的均为 is_active=False）不应出现。
            assert all(item["name"] != "iso_scene_a" for item in options["options"])
        finally:
            db_session.delete(scene)
            db_session.commit()


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

    def test_scene_id_param_and_scene_options_endpoint(self, client, db_session):
        _clear_detection(db_session)
        headers = self._grant_manager(client, db_session, "scene_api_user")
        scene = _make_scene(db_session, "scene_api_scene")
        other_scene = _make_scene(db_session, "scene_api_other")
        user = db_session.query(User).filter_by(username="scene_api_user").one()
        _make_task_with_defects(
            db_session, user.id, scene.id,
            datetime.now() - timedelta(days=1), [("hole", "破洞", "a.jpg")],
        )
        _make_task_with_defects(
            db_session, user.id, other_scene.id,
            datetime.now() - timedelta(days=1), [("stain", "污渍", "b.jpg")],
        )

        isolated = client.get(
            f"/api/dashboard/statistics?days=7&scene_id={scene.id}", headers=headers
        )
        assert isolated.status_code == 200
        assert isolated.json()["total_tasks"] == 1
        assert isolated.json()["scene_id"] == scene.id

        options = client.get(
            f"/api/dashboard/defect-options?days=7&scene_id={scene.id}",
            headers=headers,
        )
        names = {item["name"] for item in options.json()["options"]}
        assert names == {"hole"}

        scene_options = client.get("/api/dashboard/scene-options", headers=headers)
        assert scene_options.status_code == 200
        assert "options" in scene_options.json()

        bad = client.get("/api/dashboard/statistics?scene_id=0", headers=headers)
        assert bad.status_code == 422


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
