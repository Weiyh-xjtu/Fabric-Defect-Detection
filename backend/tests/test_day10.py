"""Day 10 数据看板、历史记录和系统设置接口测试。"""

from datetime import datetime

import pytest

from app.entity.db_models import (
    DetectionResult,
    DetectionScene,
    DetectionTask,
    Role,
    User,
)


def register_and_login(client, username: str) -> dict[str, str]:
    """注册测试用户并返回认证请求头。"""
    password = "123456"
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": password,
        },
    )
    assert response.status_code == 201
    login_response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create_scene(db_session, name: str) -> DetectionScene:
    """创建测试场景；保持停用以免污染其他用例的启用场景列表。"""
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


def create_detection_task(
    db_session,
    user_id: int,
    scene_id: int,
    task_type: str = "single",
) -> DetectionTask:
    """创建带一条目标结果的已完成检测任务。"""
    task = DetectionTask(
        user_id=user_id,
        scene_id=scene_id,
        task_type=task_type,
        status="completed",
        total_images=1,
        total_objects=1,
        total_inference_time=12.5,
        conf_threshold=0.25,
        iou_threshold=0.45,
        completed_at=datetime.now(),
    )
    db_session.add(task)
    db_session.flush()
    db_session.add(
        DetectionResult(
            task_id=task.id,
            image_path="sample.jpg",
            class_name="hole",
            class_name_cn="破洞",
            class_id=0,
            confidence=0.92,
            bbox=[1, 2, 30, 40],
            inference_time=12.5,
        )
    )
    db_session.commit()
    db_session.refresh(task)
    return task


class TestDashboardApi:
    """数据看板接口测试。"""

    def test_dashboard_aggregates_only_current_user(self, client, db_session):
        headers = register_and_login(client, "day10_dashboard_user")
        other_headers = register_and_login(client, "day10_dashboard_other")
        del other_headers
        user = db_session.query(User).filter_by(username="day10_dashboard_user").one()
        other = db_session.query(User).filter_by(username="day10_dashboard_other").one()
        scene = create_scene(db_session, "day10_dashboard_scene")
        create_detection_task(db_session, user.id, scene.id)
        create_detection_task(db_session, other.id, scene.id, task_type="video")

        statistics = client.get(
            "/api/dashboard/statistics?days=30", headers=headers
        )
        assert statistics.status_code == 200
        assert statistics.json()["total_tasks"] == 1
        assert statistics.json()["total_images"] == 1
        assert statistics.json()["total_objects"] == 1

        trend = client.get("/api/dashboard/trend?days=7", headers=headers)
        assert trend.status_code == 200
        assert len(trend.json()["trend"]) == 7
        assert sum(item["task_count"] for item in trend.json()["trend"]) == 1

        class_dist = client.get("/api/dashboard/class-dist", headers=headers)
        type_dist = client.get("/api/dashboard/type-dist", headers=headers)
        scene_dist = client.get("/api/dashboard/scene-dist", headers=headers)
        assert class_dist.json()["distribution"] == [{"name": "hole", "value": 1}]
        assert type_dist.json()["distribution"] == [
            {"name": "单图检测", "value": 1}
        ]
        assert scene_dist.json()["distribution"] == [
            {"name": "织物缺陷检测", "value": 1}
        ]


class TestHistoryApi:
    """检测历史记录接口测试。"""

    def test_history_filter_detail_isolation_and_delete(self, client, db_session):
        headers = register_and_login(client, "day10_history_user")
        other_headers = register_and_login(client, "day10_history_other")
        user = db_session.query(User).filter_by(username="day10_history_user").one()
        scene = create_scene(db_session, "day10_history_scene")
        task = create_detection_task(db_session, user.id, scene.id, task_type="batch")

        task_list = client.get(
            "/api/history/tasks",
            params={"task_type": "batch", "status": "completed", "keyword": task.id},
            headers=headers,
        )
        assert task_list.status_code == 200
        assert task_list.json()["total"] == 1
        assert task_list.json()["items"][0]["scene_name"] == "织物缺陷检测"

        detail = client.get(f"/api/history/tasks/{task.id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["class_counts"] == {"破洞": 1}
        assert detail.json()["results"][0]["bbox"] == [1, 2, 30, 40]

        forbidden = client.get(
            f"/api/history/tasks/{task.id}", headers=other_headers
        )
        assert forbidden.status_code == 404

        deleted = client.delete(f"/api/history/tasks/{task.id}", headers=headers)
        assert deleted.status_code == 200
        assert client.get(f"/api/history/tasks/{task.id}", headers=headers).status_code == 404
        assert (
            db_session.query(DetectionResult)
            .filter(DetectionResult.task_id == task.id)
            .count()
            == 0
        )

    def test_history_rejects_reversed_date_range(self, client):
        headers = register_and_login(client, "day10_history_dates")
        response = client.get(
            "/api/history/tasks",
            params={"start_date": "2026-07-15", "end_date": "2026-07-01"},
            headers=headers,
        )
        assert response.status_code == 422


class TestUserApi:
    """用户查询、资料和密码修改接口测试。"""

    def test_user_list_roles_profile_and_password(self, client, db_session):
        headers = register_and_login(client, "day10_settings_user")
        db_session.add(
            Role(
                name="day10_operator",
                display_name="检测员",
                description="Day 10 测试角色",
                is_system=True,
            )
        )
        db_session.commit()

        users = client.get(
            "/api/user/list",
            params={"keyword": "day10_settings_user"},
            headers=headers,
        )
        assert users.status_code == 200
        assert users.json()["total"] == 1
        assert "hashed_password" not in users.json()["items"][0]

        roles = client.get("/api/user/roles", headers=headers)
        assert roles.status_code == 200
        assert any(role["name"] == "day10_operator" for role in roles.json()["roles"])

        profile = client.put(
            "/api/user/profile",
            params={
                "email": "day10_settings_updated@example.com",
                "phone": "13800138000",
            },
            headers=headers,
        )
        assert profile.status_code == 200
        assert profile.json()["user"]["phone"] == "13800138000"

        wrong_password = client.put(
            "/api/user/password",
            params={"old_password": "wrong", "new_password": "654321"},
            headers=headers,
        )
        assert wrong_password.status_code == 400

        changed = client.put(
            "/api/user/password",
            params={"old_password": "123456", "new_password": "654321"},
            headers=headers,
        )
        assert changed.status_code == 200
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "day10_settings_user", "password": "123456"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "day10_settings_user", "password": "654321"},
            ).status_code
            == 200
        )


@pytest.mark.parametrize(
    "path",
    [
        "/api/dashboard/statistics",
        "/api/history/tasks",
        "/api/user/list",
    ],
)
def test_day10_endpoints_require_authentication(client, path: str):
    """Day 10 查询接口均要求 JWT。"""
    assert client.get(path).status_code == 401
