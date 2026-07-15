"""Detection API regression tests."""

import json
import os
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from sqlalchemy.orm import sessionmaker

from app.api.detection import _resolve_camera_device, _validate_ip_camera_url
from app.entity.db_models import DetectionResult, DetectionScene, DetectionTask, User
import app.services.detection_service as detection_service_module
from app.services.detection_service import detection_service
from app.agent.memory import conversation_memory
from app.agent.detection_agent import (
    _current_session_id,
    _current_user_id,
    list_session_attachments,
)


def list_attachments_for(session_id, user_id) -> dict:
    """模拟 Agent 请求上下文，调用会话附件查询工具并解析结果。"""
    token_session = _current_session_id.set(session_id)
    token_user = _current_user_id.set(user_id)
    try:
        return json.loads(list_session_attachments.invoke({}))
    finally:
        _current_session_id.reset(token_session)
        _current_user_id.reset(token_user)


class FakeDetectionResult:
    """提供 detect_batch 所需的最小 Ultralytics 结果接口。"""

    def __init__(self) -> None:
        self.speed = {"inference": 8.5}
        self.boxes = [
            SimpleNamespace(
                cls=[0],
                conf=[0.91],
                xyxy=np.array([[1.0, 2.0, 30.0, 40.0]]),
            )
        ]

    def plot(self) -> np.ndarray:
        """返回可供 OpenCV 编码的空白标注图。"""
        return np.zeros((32, 32, 3), dtype=np.uint8)


class FakeDetectionModel:
    """避免文件名持久化测试加载真实 YOLO 权重。"""

    names = {0: "hole"}

    def predict(self, **_kwargs) -> list[FakeDetectionResult]:
        """返回固定的一条检测结果。"""
        return [FakeDetectionResult()]


def prepare_persistence_test(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> tuple[User, DetectionScene]:
    """创建用户/场景并让检测服务使用 pytest 数据库。"""
    user = User(
        username=f"filename_user_{suffix}",
        email=f"filename_{suffix}@example.com",
        hashed_password="not-used",
    )
    scene = DetectionScene(
        name=f"filename_scene_{suffix}",
        display_name="织物缺陷检测",
        category="industry",
        class_names=["hole"],
        is_active=False,
    )
    db_session.add_all([user, scene])
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(scene)

    test_session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    monkeypatch.setattr(detection_service_module, "SessionLocal", test_session_factory)
    monkeypatch.setattr(
        detection_service,
        "_get_model",
        lambda _scene_id: FakeDetectionModel(),
    )
    return user, scene


def test_camera_cpu_device_does_not_hide_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU mode must not mutate process-wide CUDA visibility."""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")

    device = _resolve_camera_device("cpu")

    assert device == torch.device("cpu")
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"


def test_camera_gpu_device_does_not_mutate_cuda_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPU mode should select CUDA through torch.device only."""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    device = _resolve_camera_device("gpu")

    assert device == torch.device("cuda:0")
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"


def test_camera_gpu_device_reports_unavailable_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPU mode should fail clearly when CUDA is unavailable."""
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    with pytest.raises(RuntimeError, match="未检测到 CUDA 设备"):
        _resolve_camera_device("gpu")


def test_camera_device_rejects_unknown_mode() -> None:
    """Only the modes exposed by the frontend are accepted."""
    with pytest.raises(ValueError, match="不支持的检测模式"):
        _resolve_camera_device("auto")


def test_batch_api_forwards_original_upload_names(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上传接口应把 UploadFile.filename 与临时文件路径一起传给服务。"""
    client.post(
        "/api/auth/register",
        json={
            "username": "batch_filename_api_user",
            "email": "batch_filename_api@example.com",
            "password": "123456",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"username": "batch_filename_api_user", "password": "123456"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    captured = {}

    def fake_detect_batch(**kwargs) -> dict:
        captured.update(kwargs)
        return {"task_id": 1, "total_images": len(kwargs["image_paths"])}

    monkeypatch.setattr(detection_service, "detect_batch", fake_detect_batch)

    response = client.post(
        "/api/detection/batch",
        files=[
            ("files", ("fabric-a.jpg", b"first", "image/jpeg")),
            ("files", ("fabric-b.png", b"second", "image/png")),
        ],
        headers=headers,
    )

    assert response.status_code == 200
    assert captured["original_filenames"] == ["fabric-a.jpg", "fabric-b.png"]
    assert all("fabric-" not in path for path in captured["image_paths"])


def test_quick_single_saves_attachment_for_chat_session(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快捷检测附件应能被同会话 Agent 的附件查询工具看到。"""
    client.post(
        "/api/auth/register",
        json={"username": "quick_memory_user", "email": "quick_memory@example.com", "password": "123456"},
    )
    login = client.post(
        "/api/auth/login",
        json={"username": "quick_memory_user", "password": "123456"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = client.get("/api/auth/me", headers=headers)
    user_id = me.json()["id"]
    session_id = "quick-detection-session"
    monkeypatch.setattr(
        detection_service,
        "detect_single",
        lambda **_kwargs: {"task_id": 1, "total_objects": 0},
    )

    response = client.post(
        "/api/detection/single",
        files={"file": ("fabric.jpg", b"image", "image/jpeg")},
        data={"session_id": session_id},
        headers=headers,
    )

    attachments = conversation_memory.load_attachments(session_id, user_id)
    history = conversation_memory.load(session_id, user_id)
    try:
        assert response.status_code == 200
        assert attachments[0]["type"] == "image"
        assert attachments[0]["filename"] == "fabric.jpg"
        assert os.path.isfile(attachments[0]["path"])
        assert history[-2]["role"] == "user"
        assert "快捷检测" in history[-2]["content"]
        assert history[-1]["role"] == "assistant"
        assert "total_objects" in history[-1]["content"]
        listing = list_attachments_for(session_id, user_id)
        assert listing["total_rounds"] == 1
        listed = listing["rounds"][-1]["attachments"][0]
        assert listed["path"] == attachments[0]["path"]
        assert listed["filename"] == "fabric.jpg"
        assert listed["file_exists"] is True
    finally:
        for attachment in attachments:
            if os.path.isfile(attachment["path"]):
                os.unlink(attachment["path"])
        conversation_memory.clear(session_id, user_id)


def test_agent_lists_attachment_rounds_with_missing_files(tmp_path):
    """附件查询工具应按轮次列出全部图片并标记已失效文件。"""
    session_id = "all-image-rounds"
    user_id = 987654
    first = {"type": "image", "path": str(tmp_path / "round-a.jpg"), "filename": "round-a.jpg"}
    second = {"type": "image", "path": str(tmp_path / "round-b.jpg"), "filename": "round-b.jpg"}
    with open(first["path"], "wb") as file:
        file.write(b"image")
    # second 不落盘，模拟历史附件文件已被清理。
    try:
        conversation_memory.clear(session_id, user_id)
        conversation_memory.save_attachments(session_id, [first], user_id)
        conversation_memory.save_attachments(session_id, [second], user_id)
        listing = list_attachments_for(session_id, user_id)
        assert listing["total_rounds"] == 2
        assert [item["round"] for item in listing["rounds"]] == [1, 2]
        assert listing["rounds"][0]["attachments"][0]["path"] == first["path"]
        assert listing["rounds"][0]["attachments"][0]["file_exists"] is True
        assert listing["rounds"][1]["attachments"][0]["file_exists"] is False
        assert listing["available_files"] == 1
        assert listing["missing_files"] == 1
    finally:
        conversation_memory.clear(session_id, user_id)


def test_batch_history_persists_original_upload_filename(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批量检测不能把 NamedTemporaryFile 路径写进历史详情。"""
    user, scene = prepare_persistence_test(db_session, monkeypatch, "batch")

    result = detection_service.detect_batch(
        image_paths=[r"C:\Temp\tmp9f3a.jpg"],
        original_filenames=["fabric-original.jpg"],
        scene_id=scene.id,
        user_id=user.id,
    )

    db_session.expire_all()
    task = db_session.get(DetectionTask, result["task_id"])
    saved_result = (
        db_session.query(DetectionResult)
        .filter(DetectionResult.task_id == result["task_id"])
        .one()
    )
    assert task.task_type == "batch"
    assert saved_result.image_path == "fabric-original.jpg"
    assert result["detections"][0]["image_path"] == "fabric-original.jpg"
    assert result["annotated_images"][0]["image_path"] == "fabric-original.jpg"


def test_zip_history_remains_batch_and_uses_archive_relative_path(
    tmp_path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ZIP 按产品口径归为批量，并保留压缩包内图片相对路径。"""
    user, scene = prepare_persistence_test(db_session, monkeypatch, "zip")
    archive_path = tmp_path / "temporary-upload.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("roll-a/defect-01.jpg", b"fake-image")

    result = detection_service.detect_zip(
        zip_path=str(archive_path),
        original_filename="fabric-batch.zip",
        scene_id=scene.id,
        user_id=user.id,
    )

    db_session.expire_all()
    task = db_session.get(DetectionTask, result["task_id"])
    saved_result = (
        db_session.query(DetectionResult)
        .filter(DetectionResult.task_id == result["task_id"])
        .one()
    )
    assert task.task_type == "batch"
    assert result["zip_filename"] == "fabric-batch.zip"
    assert saved_result.image_path == "roll-a/defect-01.jpg"


def test_ip_camera_url_accepts_private_lan_http_url() -> None:
    assert (
        _validate_ip_camera_url(" http://192.168.1.23:8080/video ")
        == "http://192.168.1.23:8080/video"
    )


@pytest.mark.parametrize(
    "camera_url",
    [
        "",
        "file:///etc/passwd",
        "http://127.0.0.1:8080/video",
        "http://localhost:8080/video",
        "http://169.254.169.254/latest/meta-data",
        "http://8.8.8.8:8080/video",
        "http://user:pass@192.168.1.23:8080/video",
    ],
)
def test_ip_camera_url_rejects_unsafe_urls(camera_url: str) -> None:
    with pytest.raises(ValueError):
        _validate_ip_camera_url(camera_url)
