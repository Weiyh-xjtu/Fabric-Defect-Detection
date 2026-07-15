import asyncio
import json
import os
import tempfile
from types import SimpleNamespace

import pytest

from app.api import chat as chat_api
from app.api.auth import get_current_user
from app.agent.detection_agent import (
    DETECTION_TOOLS,
    _current_attachment_names,
    _current_session_id,
    _current_user_id,
    _finalize_tool_result,
    _last_full_tool_result,
    _append_attachment_context,
    _strip_base64_for_llm,
    detect_batch_images,
    list_session_attachments,
    query_system_roles,
    query_system_users,
)
from app.agent.memory import conversation_memory
from app.services.detection_service import detection_service
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


@pytest.mark.asyncio
async def test_full_tool_result_survives_tool_thread_context():
    result = {
        "total_objects": 1,
        "annotated_images": [
            {
                "image_path": "sample.jpg",
                "annotated_image_base64": "preview-data",
            }
        ],
    }
    holder = {"result": None}
    token = _last_full_tool_result.set(holder)
    try:
        slim_result = await asyncio.to_thread(_finalize_tool_result, result)

        assert "preview-data" not in slim_result
        frontend_result = json.loads(holder["result"])
        assert (
            frontend_result["annotated_images"][0]["annotated_image_base64"]
            == "preview-data"
        )
    finally:
        _last_full_tool_result.reset(token)


def test_agent_registers_user_and_role_query_tools():
    """Day 10 用户与权限查询工具已绑定，且未登录上下文不能调用。"""
    tool_names = {item.name for item in DETECTION_TOOLS}

    assert "query_system_users" in tool_names
    assert "query_system_roles" in tool_names
    assert "需要登录" in query_system_users.invoke({})
    assert "需要登录" in query_system_roles.invoke({})


def test_batch_tool_forwards_chat_attachment_original_names(monkeypatch):
    """对话附件转为服务器路径后，批量工具仍应透传浏览器原始文件名。"""
    captured = {}

    def fake_detect_batch(image_paths, **kwargs):
        captured["image_paths"] = image_paths
        captured.update(kwargs)
        return {"task_id": 1, "total_images": len(image_paths)}

    monkeypatch.setattr(detection_service, "detect_batch", fake_detect_batch)
    paths = ["C:/uploads/uuid_a.jpg", "C:/uploads/uuid_b.jpg"]
    token = _current_attachment_names.set(
        {paths[0]: "fabric-a.jpg", paths[1]: "fabric-b.jpg"}
    )
    try:
        detect_batch_images.invoke({"image_paths": paths})
    finally:
        _current_attachment_names.reset(token)

    assert captured["original_filenames"] == ["fabric-a.jpg", "fabric-b.jpg"]


def test_list_session_attachments_filters_type_and_merges_names(tmp_path):
    """附件查询工具应支持类型过滤，并把历史文件名并入请求级映射。"""
    session_id = "pytest-list-attachments"
    user_id = 24680
    video = {"type": "video", "path": str(tmp_path / "uuid_line.mp4"), "filename": "line.mp4"}
    image = {"type": "image", "path": str(tmp_path / "uuid_fabric.jpg"), "filename": "fabric.jpg"}
    for item in (video, image):
        with open(item["path"], "wb") as file:
            file.write(b"data")
    conversation_memory.clear(session_id, user_id)
    conversation_memory.save_attachments(session_id, [video], user_id)
    conversation_memory.save_attachments(session_id, [image], user_id)
    name_map = {}
    token_session = _current_session_id.set(session_id)
    token_user = _current_user_id.set(user_id)
    token_names = _current_attachment_names.set(name_map)
    try:
        listing = json.loads(
            list_session_attachments.invoke({"attachment_type": "image"})
        )
    finally:
        _current_session_id.reset(token_session)
        _current_user_id.reset(token_user)
        _current_attachment_names.reset(token_names)
        conversation_memory.clear(session_id, user_id)

    assert listing["total_rounds"] == 1
    # 类型过滤后仍保留原始轮次编号，方便 LLM 按“第N轮”对应。
    assert listing["rounds"][0]["round"] == 2
    listed = listing["rounds"][0]["attachments"][0]
    assert listed["type"] == "image"
    assert listed["file_exists"] is True
    # 历史附件的原始文件名并入请求级映射，复检落库时仍显示浏览器文件名。
    assert name_map[image["path"]] == "fabric.jpg"
    assert video["path"] not in name_map


def test_list_session_attachments_requires_session_context():
    """无会话上下文时应返回明确错误而不是空列表。"""
    listing = json.loads(list_session_attachments.invoke({}))
    assert "无法查询历史附件" in listing["error"]
