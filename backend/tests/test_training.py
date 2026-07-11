"""训练 API 测试。"""

from app.entity.db_models import DetectionScene


def _auth_headers(client):
    """注册并登录测试用户，返回认证请求头。"""
    client.post(
        "/api/auth/register",
        json={
            "username": "training_scene_user",
            "email": "training_scene@example.com",
            "password": "123456",
        },
    )
    response = client.post(
        "/api/auth/login",
        json={"username": "training_scene_user", "password": "123456"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_training_scenes_returns_active_scenes(client, db_session):
    """训练场景接口仅返回数据库中启用的场景。"""
    active_scene = DetectionScene(
        name="fdd",
        display_name="织物缺陷检测",
        category="industry",
        class_names=["defect"],
        is_active=True,
    )
    inactive_scene = DetectionScene(
        name="archived",
        display_name="已停用场景",
        category="industry",
        class_names=["defect"],
        is_active=False,
    )
    db_session.add_all([active_scene, inactive_scene])
    db_session.commit()

    response = client.get("/api/training/scenes", headers=_auth_headers(client))

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": active_scene.id,
                "name": "fdd",
                "display_name": "织物缺陷检测",
            }
        ]
    }
