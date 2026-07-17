"""全局模型管理 API 与检测模型记录测试。"""

from pathlib import Path

from sqlalchemy.orm import sessionmaker

import app.services.detection_service as detection_service_module
from app.entity.db_models import (
    DetectionScene,
    DetectionTask,
    ModelVersion,
    Role,
    TrainingTask,
    User,
    UserRole,
)
from app.services.detection_service import detection_service
from app.services.model_management_service import model_management_service
from app.training.training_service import training_service


def _admin_headers(client, db_session, username: str) -> dict[str, str]:
    client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "123456",
        },
    )
    user = db_session.query(User).filter_by(username=username).one()
    role = db_session.query(Role).filter_by(name="system_admin").one()
    db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.commit()
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": "123456"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_version(
    db_session,
    tmp_path: Path,
    suffix: str,
    *,
    global_default: bool = False,
    with_task: bool = True,
) -> ModelVersion:
    scene = DetectionScene(
        name=f"model_scene_{suffix}",
        display_name=f"模型场景 {suffix}",
        category="industry",
        class_names=["defect"],
        is_active=False,
    )
    user = User(
        username=f"model_owner_{suffix}",
        email=f"model_owner_{suffix}@example.com",
        hashed_password="not-used",
    )
    db_session.add_all([scene, user])
    db_session.flush()
    task = None
    if with_task:
        task = TrainingTask(
            user_id=user.id,
            scene_id=scene.id,
            task_uuid=f"model-task-{suffix}",
            status="completed",
            model_name="yolo11n",
        )
        db_session.add(task)
        db_session.flush()
    weight_path = tmp_path / f"{suffix}.pt"
    weight_path.write_bytes(b"fake-model")
    version = ModelVersion(
        scene_id=scene.id,
        training_task_id=task.id if task else None,
        version=f"v-{suffix}",
        model_name=f"fabric-{suffix}",
        model_type="yolo11n",
        model_path=str(weight_path),
        status="active",
        is_global_default=global_default,
    )
    db_session.add(version)
    db_session.commit()
    db_session.refresh(version)
    return version


def _clear_global_model(db_session) -> None:
    db_session.query(ModelVersion).update(
        {"is_global_default": False},
        synchronize_session=False,
    )
    db_session.commit()


def test_model_management_requires_model_permission(client) -> None:
    client.post(
        "/api/auth/register",
        json={
            "username": "model_permission_user",
            "email": "model_permission_user@example.com",
            "password": "123456",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"username": "model_permission_user", "password": "123456"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.get("/api/models", headers=headers).status_code == 403


def test_activate_model_switches_single_global_version(client, db_session, tmp_path) -> None:
    _clear_global_model(db_session)
    first = _create_version(db_session, tmp_path, "activate-a", global_default=True)
    second = _create_version(db_session, tmp_path, "activate-b")
    headers = _admin_headers(client, db_session, "model_activate_admin")
    try:
        response = client.post(f"/api/models/{second.id}/activate", headers=headers)

        assert response.status_code == 200
        assert response.json()["model"]["id"] == second.id
        db_session.expire_all()
        assert db_session.get(ModelVersion, first.id).is_global_default is False
        assert db_session.get(ModelVersion, second.id).is_global_default is True
        current = client.get("/api/models/current", headers=headers)
        assert current.status_code == 200
        assert current.json()["id"] == second.id
    finally:
        _clear_global_model(db_session)


def test_model_test_endpoint_uses_requested_version(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    version = _create_version(db_session, tmp_path, "predict")
    headers = _admin_headers(client, db_session, "model_predict_admin")
    captured = {}

    def fake_predict(item, image_path, *, filename, conf, iou):
        captured.update(
            item_id=item.id,
            image_exists=Path(image_path).is_file(),
            filename=filename,
            conf=conf,
            iou=iou,
        )
        return {"model_version_id": item.id, "total_objects": 0}

    monkeypatch.setattr(model_management_service, "predict_image", fake_predict)
    response = client.post(
        f"/api/models/{version.id}/test",
        headers=headers,
        files={"file": ("fabric.jpg", b"image", "image/jpeg")},
        data={"conf": "0.3", "iou": "0.5"},
    )

    assert response.status_code == 200
    assert response.json()["model_version_id"] == version.id
    assert captured == {
        "item_id": version.id,
        "image_exists": True,
        "filename": "fabric.jpg",
        "conf": 0.3,
        "iou": 0.5,
    }


def test_model_evaluation_reuses_associated_training_task(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    version = _create_version(db_session, tmp_path, "evaluate")
    headers = _admin_headers(client, db_session, "model_evaluate_admin")
    captured = {}

    def fake_start_validation(**kwargs):
        captured.update(kwargs)
        return {
            "task_id": version.training_task_id,
            "status": "running",
            "split": kwargs["split"],
            "message": "started",
        }

    monkeypatch.setattr(training_service, "start_validation", fake_start_validation)
    response = client.post(
        f"/api/models/{version.id}/evaluate",
        headers=headers,
        json={"split": "test", "conf": 0.01, "iou": 0.55},
    )

    assert response.status_code == 200
    assert response.json()["model_version_id"] == version.id
    assert captured["task_id"] == version.training_task_id
    assert captured["split"] == "test"


def test_detection_task_records_global_model_metadata(
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    version = _create_version(db_session, tmp_path, "detect-record")
    user = db_session.query(User).filter_by(username="model_owner_detect-record").one()

    class FakeResult:
        boxes = []
        speed = {"inference": 2.5}

        @staticmethod
        def plot():
            import numpy as np

            return np.zeros((16, 16, 3), dtype=np.uint8)

    class FakeModel:
        names = {0: "defect"}
        _platform_model_version_id = version.id
        _platform_model_version = version.version
        _platform_scene_id = version.scene_id

        @staticmethod
        def predict(**_kwargs):
            return [FakeResult()]

    test_session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    monkeypatch.setattr(detection_service_module, "SessionLocal", test_session_factory)
    monkeypatch.setattr(detection_service, "_get_model", lambda _scene_id: FakeModel())
    monkeypatch.setattr(
        "app.services.detection_service.MinIOClient.upload_bytes",
        lambda *_args, **_kwargs: None,
    )

    result = detection_service.detect_single(
        image_path=str(tmp_path / "input.jpg"),
        user_id=user.id,
    )

    task = db_session.get(DetectionTask, result["task_id"])
    assert result["model_version_id"] == version.id
    assert task.model_version_id == version.id
    assert task.scene_id == version.scene_id
