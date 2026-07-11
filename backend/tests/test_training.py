"""训练 API 测试。"""

from app.entity.db_models import DetectionScene, TrainingMetric
from app.training.training_service import TrainingService


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


def test_parse_final_results_does_not_add_an_extra_epoch(db_session, tmp_path):
    """Ultralytics 的 results.csv 使用从 1 开始的 epoch 编号。"""
    task_id = 1
    task_uuid = "epoch-test"
    results_dir = tmp_path / f"task_{task_uuid}"
    results_dir.mkdir()
    (results_dir / "results.csv").write_text(
        "epoch,train/box_loss\n1,2.5\n2,2.4\n3,2.3\n4,2.2\n5,2.1\n",
        encoding="utf-8",
    )

    for epoch in range(1, 6):
        db_session.add(TrainingMetric(task_id=task_id, epoch=epoch))
    db_session.commit()

    TrainingService._parse_final_results(
        db_session,
        task_id=task_id,
        task_uuid=task_uuid,
        config={},
        project_path=str(tmp_path),
    )

    epochs = [
        metric.epoch
        for metric in (
            db_session.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task_id)
            .order_by(TrainingMetric.epoch.asc())
            .all()
        )
    ]
    assert epochs == [1, 2, 3, 4, 5]
