import os
import tempfile
from types import SimpleNamespace

import pytest

from app.api import chat as chat_api
from app.api.auth import get_current_user
from app.agent.detection_agent import (
    _append_attachment_context,
    _strip_base64_for_llm,
)
from main import app


@pytest.fixture
def chat_client(client):
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1,
        username="chat_test_user",
    )
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _remove_uploaded(attachments):
    for attachment in attachments:
        try:
            os.unlink(attachment["path"])
        except OSError:
            pass


def test_upload_multiple_images(chat_client):
    response = chat_client.post(
        "/api/chat/upload",
        files=[
            ("files", ("first.jpg", b"first-image", "image/jpeg")),
            ("files", ("second.png", b"second-image", "image/png")),
        ],
    )

    assert response.status_code == 200
    attachments = response.json()["attachments"]
    try:
        assert [item["type"] for item in attachments] == ["image", "image"]
        assert all(os.path.isfile(item["path"]) for item in attachments)
    finally:
        _remove_uploaded(attachments)


def test_upload_video_attachment(chat_client):
    response = chat_client.post(
        "/api/chat/upload",
        files=[("files", ("sample.mp4", b"video-data", "video/mp4"))],
    )

    assert response.status_code == 200
    attachments = response.json()["attachments"]
    try:
        assert attachments[0]["type"] == "video"
        assert attachments[0]["filename"] == "sample.mp4"
    finally:
        _remove_uploaded(attachments)


def test_upload_rejects_mixed_attachment_types(chat_client):
    response = chat_client.post(
        "/api/chat/upload",
        files=[
            ("files", ("sample.jpg", b"image", "image/jpeg")),
            ("files", ("sample.zip", b"zip", "application/zip")),
        ],
    )

    assert response.status_code == 400
    assert "混合" in str(response.json())


def test_chat_stream_passes_structured_attachments(
    chat_client,
    monkeypatch,
):
    fd, attachment_path = tempfile.mkstemp(
        dir=chat_api.UPLOAD_DIR,
        suffix=".zip",
    )
    os.close(fd)
    captured = {}

    async def fake_chat_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "text_chunk", "content": "已收到附件"}

    monkeypatch.setattr(chat_api.detection_agent, "chat_stream", fake_chat_stream)

    try:
        response = chat_client.post(
            "/api/chat/stream",
            json={
                "message": "请检测",
                "attachments": [
                    {
                        "type": "zip",
                        "path": attachment_path,
                        "filename": "images.zip",
                    }
                ],
            },
        )

        assert response.status_code == 200
        assert "已收到附件" in response.text
        assert captured["attachments"][0]["type"] == "zip"
        assert captured["attachments"][0]["path"] == attachment_path
        assert not os.path.exists(attachment_path)
    finally:
        if os.path.exists(attachment_path):
            os.unlink(attachment_path)


def test_agent_builds_attachment_tool_context():
    batch_message = _append_attachment_context(
        "请检测",
        [
            {"type": "image", "path": "C:/uploads/first.jpg"},
            {"type": "image", "path": "C:/uploads/second.jpg"},
        ],
    )
    video_message = _append_attachment_context(
        "请检测",
        [{"type": "video", "path": "C:/uploads/sample.mp4"}],
    )

    assert "附件图片路径列表" in batch_message
    assert "first.jpg" in batch_message and "second.jpg" in batch_message
    assert "附件视频路径" in video_message


def test_video_result_is_slimmed_without_mutating_frontend_result():
    result = {
        "type": "video",
        "annotated_video_url": "http://example.test/video.mp4",
        "key_frames": [
            {
                "frame_index": 0,
                "annotated_image_base64": "base64-data",
            }
        ],
    }

    slim = _strip_base64_for_llm(result)

    assert "annotated_video_url" not in slim
    assert "annotated_image_base64" not in slim["key_frames"][0]
    assert result["annotated_video_url"]
    assert result["key_frames"][0]["annotated_image_base64"] == "base64-data"
