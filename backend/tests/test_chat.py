import asyncio
import json
import os
import tempfile
import uuid
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
        is_superuser=True,
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


def test_update_session_title_only_updates_current_users_session(
    chat_client,
    db_session,
):
    from app.entity.db_models import ChatSession

    own_uuid = f"rename-own-{uuid.uuid4().hex}"
    other_uuid = f"rename-other-{uuid.uuid4().hex}"
    own_session = ChatSession(
        user_id=1,
        session_uuid=own_uuid,
        title="原标题",
        message_count=2,
    )
    other_session = ChatSession(
        user_id=2,
        session_uuid=other_uuid,
        title="其他用户标题",
        message_count=1,
    )
    db_session.add_all([own_session, other_session])
    db_session.commit()

    try:
        response = chat_client.patch(
            f"/api/chat/sessions/{own_uuid}",
            json={"title": "  更新后的标题  "},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "更新后的标题"
        db_session.refresh(own_session)
        assert own_session.title == "更新后的标题"

        forbidden = chat_client.patch(
            f"/api/chat/sessions/{other_uuid}",
            json={"title": "越权修改"},
        )
        assert forbidden.status_code == 404

        blank = chat_client.patch(
            f"/api/chat/sessions/{own_uuid}",
            json={"title": "   "},
        )
        assert blank.status_code == 422
    finally:
        db_session.delete(own_session)
        db_session.delete(other_session)
        db_session.commit()


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
    captured_upload = {}

    async def fake_chat_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "text_chunk", "content": "已收到附件"}

    def fake_upload_user_refs(user_id, attachments):
        captured_upload["user_id"] = user_id
        captured_upload["attachments"] = attachments
        return [{
            "source": "user",
            "type": "zip",
            "filename": "images.zip",
            "object_name": "chat-originals/1/images.zip",
        }]

    monkeypatch.setattr(chat_api.detection_agent, "chat_stream", fake_chat_stream)
    monkeypatch.setattr(chat_api, "_upload_user_attachment_refs", fake_upload_user_refs)

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
        assert captured_upload["user_id"] == 1
        assert captured_upload["attachments"][0]["filename"] == "images.zip"
        assert not os.path.exists(attachment_path)
    finally:
        if os.path.exists(attachment_path):
            os.unlink(attachment_path)


def test_chat_stream_persists_comma_agent_used(chat_client, monkeypatch):
    """并行 agent_route 携带 agents 列表时，assistant 落库为逗号连接的多专家名，
    且 tool_call 的 agent 归属被持久化，供历史徽标还原。"""
    from tests.conftest import TestSessionLocal
    from app.entity.db_models import ChatMessage, ChatSession

    monkeypatch.setattr(chat_api, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(chat_api, "_upload_user_attachment_refs", lambda user_id, attachments: [])
    monkeypatch.setattr(chat_api, "_extract_attachment_refs", lambda tool_results: [])

    session_uuid = "parallel-agent-used-uuid-1"

    async def fake_chat_stream(**kwargs):
        yield {"type": "agent_route", "agent": "detection", "agents": ["detection", "qa"]}
        yield {"type": "tool_call", "tool": "detect_single_image", "input": {}, "agent": "detection"}
        yield {"type": "tool_result", "tool": "detect_single_image", "result": '{"total_objects":1}', "agent": "detection"}
        yield {"type": "text_chunk", "content": "#### 🔍 检测专家\n\n检出1个目标", "agent": "detection"}
        yield {"type": "text_chunk", "content": "\n\n---\n\n#### 📖 知识问答\n\nYOLO是单阶段检测算法", "agent": "qa"}

    monkeypatch.setattr(chat_api.detection_agent, "chat_stream", fake_chat_stream)

    response = chat_client.post(
        "/api/chat/stream",
        json={"message": "检测这张图片，并告诉我什么是YOLO", "session_id": session_uuid},
    )
    assert response.status_code == 200
    assert "检测专家" in response.text
    assert "知识问答" in response.text

    db = TestSessionLocal()
    try:
        session = db.query(ChatSession).filter(
            ChatSession.session_uuid == session_uuid
        ).first()
        assert session is not None
        assistant = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session.id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.id.desc())
            .first()
        )
        assert assistant is not None
        assert assistant.agent_used == "detection,qa"
        assert assistant.tool_calls[0]["agent"] == "detection"
    finally:
        db.close()


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


def test_minio_object_name_from_url_roundtrip():
    """预签名 URL 应能反解回永久 object_name（含 URL 编码还原）。"""
    from app.storage.minio_client import MinIOClient

    # 绕过 __init__ 的网络连接，仅测纯字符串解析逻辑。
    client = object.__new__(MinIOClient)
    client.bucket_name = "rsod"

    url = "http://localhost:9000/rsod/detections/12/a%20b.jpg?X-Amz-Signature=abc"
    assert client.object_name_from_url(url) == "detections/12/a b.jpg"
    # 桶名不匹配或空输入返回 None
    assert client.object_name_from_url("http://localhost:9000/other/x.jpg") is None
    assert client.object_name_from_url("") is None


def test_slim_tool_result_strips_base64_but_keeps_stats():
    """落库前应剥离 base64，但保留统计信息与 MinIO URL。"""
    raw = json.dumps({
        "total_objects": 2,
        "class_counts": {"hole": 2},
        "annotated_image_url": "http://minio/bucket/detections/1/a.jpg?sig=x",
        "annotated_image_base64": "huge-base64-data",
    })

    slim = json.loads(chat_api._slim_tool_result(raw))

    assert slim["total_objects"] == 2
    assert slim["class_counts"] == {"hole": 2}
    assert "annotated_image_base64" not in slim
    # URL 是短标识，保留以便调试；真正的持久还原走 attachments。
    assert slim["annotated_image_url"].startswith("http://minio")


def test_slim_tool_result_tolerates_non_json():
    assert chat_api._slim_tool_result("") == ""
    assert chat_api._slim_tool_result("not-json") == "not-json"


class _FakeMinio:
    """用 object_name 直接换签的 MinIO 桩，避免依赖真实服务。"""

    bucket_name = "bucket"

    def __init__(self, ensure_bucket=True):
        self.ensure_bucket = ensure_bucket

    def upload_file(self, object_name, file_path, content_type="application/octet-stream"):
        return f"http://minio/{self.bucket_name}/{object_name}?uploaded=1"

    def object_name_from_url(self, url):
        if not url:
            return None
        prefix = f"http://minio/{self.bucket_name}/"
        if not url.startswith(prefix):
            return None
        return url[len(prefix):].split("?", 1)[0]

    def presign_from_url_or_name(self, value):
        if not value:
            return None
        name = self.object_name_from_url(value) if value.startswith("http") else value
        return f"http://minio/{self.bucket_name}/{name}?fresh=1" if name else None

    def browser_url_from_url_or_name(
        self,
        value,
        *,
        filename=None,
        content_type=None,
    ):
        del filename, content_type
        if not value:
            return None
        name = self.object_name_from_url(value) if value.startswith("http") else value
        return f"/api/files/{name}" if name else None


def test_upload_user_attachment_refs_stores_only_minio_reference(monkeypatch, tmp_path):
    uploaded = []

    class _UploadingMinio(_FakeMinio):
        def upload_file(self, object_name, file_path, content_type="application/octet-stream"):
            uploaded.append((object_name, file_path, content_type))
            return f"http://minio/{self.bucket_name}/{object_name}?uploaded=1"

    source = tmp_path / "original.jpg"
    source.write_bytes(b"jpeg-data")
    monkeypatch.setattr(chat_api, "MinIOClient", _UploadingMinio)

    refs = chat_api._upload_user_attachment_refs(
        7,
        [{"type": "image", "path": str(source), "filename": "fabric.jpg"}],
    )

    assert len(refs) == 1
    assert refs[0]["source"] == "user"
    assert refs[0]["type"] == "image"
    assert refs[0]["filename"] == "fabric.jpg"
    assert refs[0]["content_type"] == "image/jpeg"
    assert refs[0]["size"] == len(b"jpeg-data")
    assert refs[0]["object_name"].startswith("chat-originals/7/")
    assert uploaded[0][2] == "image/jpeg"
    assert "jpeg-data" not in json.dumps(refs)


def test_extract_attachment_refs_covers_image_batch_video(monkeypatch):
    monkeypatch.setattr(chat_api, "MinIOClient", _FakeMinio)
    tool_results = [
        {"tool": "detect_single_image", "result": json.dumps({
            "total_objects": 1,
            "annotated_image_url": "http://minio/bucket/detections/1/a.jpg?sig=x",
            "annotated_image_base64": "x",
        })},
        {"tool": "detect_batch_images", "result": json.dumps({
            "total_objects": 3,
            "annotated_images": [
                {"image_path": "b.jpg", "annotated_image_url": "http://minio/bucket/detections/2/b.jpg?sig=y"},
                {"image_path": "c.jpg", "annotated_image_url": "http://minio/bucket/detections/2/c.jpg?sig=z"},
            ],
        })},
        {"tool": "detect_video_file", "result": json.dumps({
            "type": "video",
            "total_objects": 5,
            "annotated_video_url": "http://minio/bucket/detections/3/annotated_video.mp4?sig=v",
        })},
    ]

    refs = chat_api._extract_attachment_refs(tool_results)

    by_type = {ref["type"]: ref for ref in refs}
    assert by_type["image"]["object_name"] == "detections/1/a.jpg"
    assert by_type["video"]["object_name"] == "detections/3/annotated_video.mp4"
    assert {img["object_name"] for img in by_type["images"]["images"]} == {
        "detections/2/b.jpg",
        "detections/2/c.jpg",
    }


def test_resign_attachments_reissues_fresh_urls(monkeypatch):
    monkeypatch.setattr(chat_api, "MinIOClient", _FakeMinio)
    stored = [
        {
            "source": "user",
            "type": "image",
            "filename": "original.jpg",
            "content_type": "image/jpeg",
            "size": 123,
            "object_name": "chat-originals/1/a.jpg",
        },
        {"tool": "detect_batch_images", "type": "images", "images": [
            {"image_path": "b.jpg", "object_name": "detections/2/b.jpg"},
        ]},
        {"tool": "detect_video_file", "type": "video", "object_name": "detections/3/v.mp4"},
    ]

    resolved = chat_api._resign_attachments(stored)

    by_type = {ref["type"]: ref for ref in resolved}
    assert by_type["image"]["url"] == "/api/files/chat-originals/1/a.jpg"
    assert by_type["image"]["source"] == "user"
    assert by_type["image"]["filename"] == "original.jpg"
    assert by_type["image"]["content_type"] == "image/jpeg"
    assert by_type["image"]["size"] == 123
    assert by_type["video"]["url"] == "/api/files/detections/3/v.mp4"
    assert by_type["images"]["images"][0]["url"] == "/api/files/detections/2/b.jpg"


def test_resign_attachments_empty_is_noop():
    assert chat_api._resign_attachments(None) == []
    assert chat_api._resign_attachments([]) == []


def test_collect_object_names_covers_all_types():
    attachments = [
        {"type": "image", "object_name": "detections/1/a.jpg"},
        {"type": "video", "object_name": "detections/3/v.mp4"},
        {"type": "images", "images": [
            {"object_name": "detections/2/b.jpg"},
            {"object_name": "detections/2/c.jpg"},
        ]},
    ]

    names = chat_api._collect_object_names(attachments)

    assert set(names) == {
        "detections/1/a.jpg",
        "detections/3/v.mp4",
        "detections/2/b.jpg",
        "detections/2/c.jpg",
    }
    assert chat_api._collect_object_names(None) == []
    assert chat_api._collect_object_names([]) == []


def test_delete_session_objects_removes_all_referenced(monkeypatch):
    deleted = []

    class _DeletingMinio(_FakeMinio):
        def delete_file(self, object_name):
            deleted.append(object_name)

    monkeypatch.setattr(chat_api, "MinIOClient", _DeletingMinio)
    messages = [
        SimpleNamespace(attachments=[
            {"type": "image", "object_name": "detections/1/a.jpg"},
        ]),
        SimpleNamespace(attachments=[
            {"type": "images", "images": [{"object_name": "detections/2/b.jpg"}]},
        ]),
        SimpleNamespace(attachments=[
            {
                "source": "user",
                "type": "video",
                "object_name": "chat-originals/1/original.mp4",
            },
        ]),
        SimpleNamespace(attachments=None),
    ]

    chat_api._delete_session_objects(messages)

    assert set(deleted) == {
        "detections/1/a.jpg",
        "detections/2/b.jpg",
        "chat-originals/1/original.mp4",
    }


def test_delete_session_objects_survives_minio_failure(monkeypatch):
    """单个对象删除失败不应抛出，以免阻断会话删除。"""
    class _FlakyMinio(_FakeMinio):
        def delete_file(self, object_name):
            raise RuntimeError("boom")

    monkeypatch.setattr(chat_api, "MinIOClient", _FlakyMinio)
    messages = [SimpleNamespace(attachments=[
        {"type": "image", "object_name": "detections/1/a.jpg"},
    ])]

    # 不抛异常即为通过
    chat_api._delete_session_objects(messages)


def test_persist_quick_detection_creates_session_and_messages(monkeypatch, tmp_path):
    """快捷检测应落库为可刷新还原的会话（用户 + 助手两条消息）。"""
    from tests.conftest import TestSessionLocal
    from app.entity.db_models import ChatMessage, ChatSession

    monkeypatch.setattr(chat_api, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(chat_api, "MinIOClient", _FakeMinio)
    session_uuid = "quick-detect-uuid-1"
    original = tmp_path / "original.jpg"
    original.write_bytes(b"original-image")
    result = {
        "total_objects": 2,
        "annotated_image_url": "http://minio/bucket/detections/9/a.jpg?sig=x",
        "annotated_image_base64": "SHOULD_NOT_PERSIST",
    }

    chat_api.persist_quick_detection(
        user_id=1,
        session_uuid=session_uuid,
        tool_name="detect_single_image",
        user_label="单图 a.jpg",
        result=result,
        original_attachments=[{
            "type": "image",
            "path": str(original),
            "filename": "original.jpg",
        }],
    )

    db = TestSessionLocal()
    try:
        session = db.query(ChatSession).filter(
            ChatSession.session_uuid == session_uuid
        ).first()
        assert session is not None
        assert session.message_count == 2
        messages = db.query(ChatMessage).filter(
            ChatMessage.session_id == session.id
        ).order_by(ChatMessage.id).all()
        assert [m.role for m in messages] == ["user", "assistant"]
        user_message, assistant = messages
        assert user_message.attachments[0]["source"] == "user"
        assert user_message.attachments[0]["type"] == "image"
        assert user_message.attachments[0]["filename"] == "original.jpg"
        assert user_message.attachments[0]["object_name"].startswith(
            "chat-originals/1/"
        )
        # 助手消息保存的是标注结果引用，不与用户原图混在同一条消息中。
        assert assistant.attachments[0]["object_name"] == "detections/9/a.jpg"
        assert "SHOULD_NOT_PERSIST" not in (assistant.tool_result or "")
        # 统计信息保留，供历史卡片重建（tool_result 为嵌套 JSON 字符串）
        inner = json.loads(json.loads(assistant.tool_result)[0]["result"])
        assert inner["total_objects"] == 2
        assert "annotated_image_base64" not in inner
    finally:
        db.close()


def test_persist_quick_detection_no_session_is_noop(monkeypatch):
    """无 session_uuid 时应静默跳过，不触碰数据库。"""
    def _boom():
        raise AssertionError("不应创建数据库会话")

    monkeypatch.setattr(chat_api, "SessionLocal", _boom)
    chat_api.persist_quick_detection(1, "", "detect_single_image", "x", {"total_objects": 0})


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
    monkeypatch.setattr(
        "app.agent.detection_agent._tool_permission_error", lambda _permission: None
    )
    paths = ["C:/uploads/uuid_a.jpg", "C:/uploads/uuid_b.jpg"]
    token = _current_attachment_names.set(
        {paths[0]: "fabric-a.jpg", paths[1]: "fabric-b.jpg"}
    )
    try:
        detect_batch_images.invoke({"image_paths": paths})
    finally:
        _current_attachment_names.reset(token)

    assert captured["original_filenames"] == ["fabric-a.jpg", "fabric-b.jpg"]


def test_list_session_attachments_filters_type_and_merges_names(tmp_path, monkeypatch):
    """附件查询工具应支持类型过滤，并把历史文件名并入请求级映射。"""
    monkeypatch.setattr(
        "app.agent.detection_agent._tool_permission_error", lambda _permission: None
    )
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


def test_list_session_attachments_requires_session_context(monkeypatch):
    """无会话上下文时应返回明确错误而不是空列表。"""
    monkeypatch.setattr(
        "app.agent.detection_agent._tool_permission_error", lambda _permission: None
    )
    listing = json.loads(list_session_attachments.invoke({}))
    assert "无法查询历史附件" in listing["error"]


def test_list_session_attachments_restores_minio_original_after_cache_loss(
    monkeypatch,
    tmp_path,
):
    """Redis/临时路径丢失后，应从当前用户的数据库引用和 MinIO 原图恢复。"""
    from app.agent import attachment_store
    from app.entity.db_models import ChatMessage, ChatSession, User
    from tests.conftest import TestSessionLocal

    marker = uuid.uuid4().hex
    session_uuid = f"restore-attachment-{marker}"
    db = TestSessionLocal()
    try:
        user = User(
            username=f"restore_user_{marker}",
            email=f"restore_{marker}@example.com",
            hashed_password="test-hash",
            is_active=True,
        )
        db.add(user)
        db.flush()
        user_id = user.id
        session = ChatSession(
            user_id=user_id,
            session_uuid=session_uuid,
            title="历史附件恢复",
        )
        db.add(session)
        db.flush()
        object_name = f"chat-originals/{user_id}/{marker}_fabric.jpg"
        db.add(
            ChatMessage(
                session_id=session.id,
                role="user",
                content="请检测这张图片",
                attachments=[
                    {
                        "source": "user",
                        "type": "image",
                        "filename": "fabric.jpg",
                        "object_name": object_name,
                    }
                ],
            )
        )
        db.commit()
    finally:
        db.close()

    downloaded = []

    class _DownloadingMinio:
        def download_file(self, stored_object_name: str, file_path: str) -> str:
            downloaded.append(stored_object_name)
            with open(file_path, "wb") as output:
                output.write(b"restored-image")
            return file_path

    monkeypatch.setattr(attachment_store, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(attachment_store, "MinIOClient", _DownloadingMinio)
    monkeypatch.setattr(attachment_store, "RESTORED_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.agent.detection_agent._tool_permission_error", lambda _permission: None
    )
    conversation_memory.clear(session_uuid, user_id)

    # 相同 session_uuid 不能被其他用户恢复，避免跨用户读取 MinIO 原件。
    token_session = _current_session_id.set(session_uuid)
    token_user = _current_user_id.set(user_id + 100000)
    try:
        other_user_listing = json.loads(list_session_attachments.invoke({}))
    finally:
        _current_session_id.reset(token_session)
        _current_user_id.reset(token_user)
    assert other_user_listing["total_rounds"] == 0
    assert downloaded == []

    name_map = {}
    token_session = _current_session_id.set(session_uuid)
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

    try:
        assert listing["total_rounds"] == 1
        assert listing["available_files"] == 1
        restored = listing["rounds"][0]["attachments"][0]
        assert restored["filename"] == "fabric.jpg"
        assert restored["file_exists"] is True
        assert os.path.isfile(restored["path"])
        assert downloaded == [object_name]
        assert name_map[restored["path"]] == "fabric.jpg"
        assert conversation_memory.load_attachment_history(
            session_uuid, user_id
        )[0][0]["path"] == restored["path"]
    finally:
        conversation_memory.clear(session_uuid, user_id)
        cleanup_db = TestSessionLocal()
        try:
            stored_session = cleanup_db.query(ChatSession).filter(
                ChatSession.session_uuid == session_uuid
            ).first()
            if stored_session is not None:
                cleanup_db.delete(stored_session)
            cleanup_db.commit()
        finally:
            cleanup_db.close()
