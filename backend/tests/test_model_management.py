"""全局模型管理 API 与检测模型记录测试。"""

from pathlib import Path

from sqlalchemy.orm import sessionmaker

import app.services.detection_service as detection_service_module
import app.services.model_management_service as model_service_module
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
from app.services.model_management_service import ModelManagementService, model_management_service
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


def test_current_scene_endpoint_follows_global_model(
    client, db_session, tmp_path
) -> None:
    """current-scene 返回全局默认模型的归属场景；普通登录用户即可访问。"""
    _clear_global_model(db_session)
    version = _create_version(db_session, tmp_path, "scene-banner", global_default=True)
    # 无需 model:manage 权限，注册用户（质检员）即可读取。
    client.post(
        "/api/auth/register",
        json={
            "username": "scene_banner_user",
            "email": "scene_banner_user@example.com",
            "password": "123456",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"username": "scene_banner_user", "password": "123456"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    try:
        response = client.get("/api/detection/current-scene", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["scene"]["id"] == version.scene_id
        assert payload["scene"]["display_name"] == "模型场景 scene-banner"
        assert payload["model_version"] == version.version
    finally:
        _clear_global_model(db_session)

    # 清除全局模型后 scene 为 null。
    response = client.get("/api/detection/current-scene", headers=headers)
    assert response.status_code == 200
    assert response.json()["scene"] is None


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

    def fake_predict(_db, item, image_path, *, filename, conf, iou):
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


def test_archive_and_unarchive_model_version(client, db_session, tmp_path) -> None:
    version = _create_version(db_session, tmp_path, "archive")
    headers = _admin_headers(client, db_session, "model_archive_admin")

    archived = client.post(f"/api/models/{version.id}/archive", headers=headers)
    assert archived.status_code == 200
    assert archived.json()["model"]["status"] == "archived"
    assert archived.json()["model"]["archived_at"] is not None
    assert Path(version.model_path).is_file()

    restored = client.post(f"/api/models/{version.id}/unarchive", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["model"]["status"] == "active"


def test_current_global_model_cannot_be_archived_or_deleted(
    client,
    db_session,
    tmp_path,
) -> None:
    _clear_global_model(db_session)
    version = _create_version(db_session, tmp_path, "protected", global_default=True)
    headers = _admin_headers(client, db_session, "model_protected_admin")
    try:
        archived = client.post(f"/api/models/{version.id}/archive", headers=headers)
        deleted = client.delete(f"/api/models/{version.id}", headers=headers)
        assert archived.status_code == 400
        assert deleted.status_code == 400
        assert Path(version.model_path).is_file()
    finally:
        _clear_global_model(db_session)


def test_delete_model_keeps_training_artifact(client, db_session, tmp_path) -> None:
    version = _create_version(db_session, tmp_path, "delete-training")
    headers = _admin_headers(client, db_session, "model_delete_training_admin")
    training_weight = Path(version.model_path)

    response = client.delete(f"/api/models/{version.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["local_action"] == "retained_training_artifact"
    assert training_weight.is_file()
    db_session.expire_all()
    deleted = db_session.get(ModelVersion, version.id)
    assert deleted.status == "deleted"
    assert deleted.deleted_at is not None


def test_minio_backup_restore_and_delete_export_copy(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    version = _create_version(db_session, tmp_path, "backup-restore")
    headers = _admin_headers(client, db_session, "model_backup_admin")
    models_dir = tmp_path / "managed-models"
    object_store = {}

    class FakeMinIOClient:
        bucket_name = "test-bucket"

        def upload_file(self, object_name, file_path, content_type="application/octet-stream"):
            del content_type
            object_store[object_name] = Path(file_path).read_bytes()
            return f"http://minio/{self.bucket_name}/{object_name}?signature=test"

        def download_file(self, object_name, file_path):
            target = Path(file_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(object_store[object_name])
            return str(target)

        def get_presigned_url(self, object_name):
            return f"http://minio/{self.bucket_name}/{object_name}?signature=fresh"

        def object_name_from_url(self, url):
            return url.split(f"/{self.bucket_name}/", 1)[1].split("?", 1)[0]

        def delete_file(self, object_name):
            object_store.pop(object_name, None)

    monkeypatch.setattr(model_service_module, "MinIOClient", FakeMinIOClient)
    monkeypatch.setattr(
        ModelManagementService,
        "_models_dir",
        classmethod(lambda _cls: models_dir),
    )

    backup = client.post(f"/api/models/{version.id}/backup", headers=headers)
    assert backup.status_code == 200
    backup_model = backup.json()["model"]
    assert backup_model["backup_available"] is True
    assert backup_model["file_sha256"]
    object_name = backup_model["minio_object_name"]
    assert object_name in object_store

    Path(version.model_path).unlink()
    restore = client.post(f"/api/models/{version.id}/restore", headers=headers)
    assert restore.status_code == 200
    restored_path = Path(restore.json()["model"]["model_path"])
    assert restored_path.is_file()
    assert restored_path.is_relative_to(models_dir)

    deleted = client.delete(f"/api/models/{version.id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["local_action"] == "deleted_export_copy"
    assert deleted.json()["minio_action"] == "deleted"
    assert not restored_path.exists()
    assert object_name not in object_store


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
