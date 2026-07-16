"""
对话相关 API 路由

接口列表：
  - POST /api/chat/upload    上传检测附件，返回服务端路径
  - POST /api/chat/stream    SSE 流式对话（核心接口）

"""

import asyncio
import json
import mimetypes
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent.multi_agent import multi_agent as detection_agent
from app.agent.detection_agent import _strip_base64_for_llm
from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.database.session import SessionLocal, get_db
from app.entity.db_models import ChatMessage, ChatSession
from app.agent.memory import conversation_memory
from app.storage.minio_client import MinIOClient

logger = get_logger(__name__)

router = APIRouter(prefix="/api/chat", tags=["智能对话"])

# 上传文件临时存储目录
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "rsod_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv"}
MAX_UPLOAD_SIZES = {
    "image": 10 * 1024 * 1024,
    "zip": 100 * 1024 * 1024,
    "video": 50 * 1024 * 1024,
}


def _attachment_type(filename: str) -> Optional[str]:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix == ".zip":
        return "zip"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def _normalize_attachments(raw_attachments: list) -> list[dict]:
    """校验聊天请求中的附件只能引用本接口上传的临时文件。"""
    normalized = []
    upload_root = os.path.abspath(UPLOAD_DIR)

    for item in raw_attachments:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="附件参数格式错误")

        attachment_type = item.get("type")
        file_path = os.path.abspath(str(item.get("path", "")))
        if attachment_type not in {"image", "zip", "video"} or not file_path:
            raise HTTPException(status_code=400, detail="附件类型或路径无效")
        try:
            common_path = os.path.commonpath([upload_root, file_path])
        except ValueError:
            common_path = ""
        if common_path != upload_root:
            raise HTTPException(status_code=400, detail="附件路径不在上传目录中")
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=400, detail="附件文件不存在或已失效")

        normalized.append(
            {
                "type": attachment_type,
                "path": file_path,
                "filename": item.get("filename") or os.path.basename(file_path),
            }
        )

    attachment_types = {item["type"] for item in normalized}
    if len(attachment_types) > 1:
        raise HTTPException(status_code=400, detail="一次消息不能混合不同类型的附件")
    if attachment_types & {"zip", "video"} and len(normalized) > 1:
        raise HTTPException(status_code=400, detail="ZIP 或视频附件一次只能上传一个")

    return normalized


def _cleanup_attachments(attachments: list[dict]) -> None:
    for attachment in attachments:
        try:
            os.unlink(attachment["path"])
        except OSError:
            pass


def _get_or_create_session(db: Session, user_id: int, session_uuid: str, title: str | None = None) -> ChatSession:
    session = db.query(ChatSession).filter(
        ChatSession.user_id == user_id,
        ChatSession.session_uuid == session_uuid,
    ).first()
    if session:
        return session
    session = ChatSession(
        user_id=user_id,
        session_uuid=session_uuid,
        title=(title or "新对话")[:200],
        status="active",
        message_count=0,
    )
    db.add(session)
    db.flush()
    return session


def _slim_tool_result(raw) -> str:
    """
    剥离工具结果中的 base64 标注图后返回 JSON 字符串，用于存库。

    base64（单图可达数万字符）只服务本轮前端即时渲染，历史改用 MinIO URL
    还原，因此不必落库。非检测结果或解析失败时按原样截断兜底。
    """
    if not raw:
        return ""
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return str(raw)[:10000]
    if not isinstance(result, dict):
        return str(raw)[:10000]
    return json.dumps(_strip_base64_for_llm(result), ensure_ascii=False)


def _extract_attachment_refs(tool_results: list[dict]) -> list[dict]:
    """
    从本轮工具结果中提取检测标注图/视频的 MinIO 永久对象引用。

    存库时把易过期的预签名 URL 归一化为 object_name，历史读回时再实时换签。
    仅记录对象标识（几十字节），绝不把 base64 或视频字节写进数据库。

    Returns:
        [{"tool":..., "type":"image|images|video", ...}]，无可持久化附件时为空列表。
    """
    try:
        minio = MinIOClient()
    except Exception as e:
        logger.warning("MinIO 不可用，跳过附件引用提取: %s", str(e))
        return []

    refs = []
    for item in tool_results:
        raw = item.get("result")
        if not raw:
            continue
        try:
            result = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(result, dict):
            continue
        tool = item.get("tool")

        # 视频：单个标注视频 URL
        video_url = result.get("annotated_video_url")
        if video_url:
            object_name = minio.object_name_from_url(video_url)
            if object_name:
                refs.append({"tool": tool, "type": "video", "object_name": object_name})
            continue

        # 批量/ZIP：多张标注图
        annotated_images = result.get("annotated_images")
        if isinstance(annotated_images, list) and annotated_images:
            images = []
            for img in annotated_images:
                if not isinstance(img, dict):
                    continue
                object_name = minio.object_name_from_url(img.get("annotated_image_url", ""))
                if object_name:
                    images.append({
                        "image_path": img.get("image_path"),
                        "object_name": object_name,
                    })
            if images:
                refs.append({"tool": tool, "type": "images", "images": images})
            continue

        # 单图：单张标注图 URL
        image_url = result.get("annotated_image_url")
        if image_url:
            object_name = minio.object_name_from_url(image_url)
            if object_name:
                refs.append({"tool": tool, "type": "image", "object_name": object_name})

    return refs


def _upload_user_attachment_refs(
    user_id: int,
    attachments: list[dict] | None,
) -> list[dict]:
    """
    把用户原始附件上传到 MinIO，并返回适合写入 ChatMessage.attachments 的引用。

    原始文件仍保留在临时目录供当前会话复检；历史展示只依赖 MinIO。数据库
    仅保存 object_name、文件名和类型，不保存文件字节、base64 或易过期 URL。
    单个附件上传失败时记录告警并继续，不阻断检测流程。
    """
    if not attachments:
        return []
    try:
        minio = MinIOClient()
    except Exception as e:
        logger.warning("MinIO 不可用，用户原始附件未持久化: %s", str(e))
        return []

    refs = []
    for item in attachments:
        path = item.get("path") if isinstance(item, dict) else None
        if not path or not os.path.isfile(path):
            continue
        filename = Path(item.get("filename") or path).name
        attachment_type = item.get("type") or "file"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        object_name = f"chat-originals/{user_id}/{uuid.uuid4().hex}_{filename}"
        try:
            minio.upload_file(object_name, path, content_type=content_type)
        except Exception as e:
            logger.warning("用户原始附件上传失败 object_name=%s: %s", object_name, str(e))
            continue
        refs.append({
            "source": "user",
            "type": attachment_type,
            "filename": filename,
            "content_type": content_type,
            "size": os.path.getsize(path),
            "object_name": object_name,
        })
    return refs


def persist_quick_detection(
    user_id: int,
    session_uuid: str,
    tool_name: str,
    user_label: str,
    result: dict,
    original_attachments: list[dict] | None = None,
) -> None:
    """
    把"快捷检测"（跳过 LLM，直接调 YOLO）的一轮结果落库。

    快捷检测不经过 /stream，若不落库则刷新后会话消失。这里复用与 /stream
    完全一致的结构写入 ChatSession + 两条 ChatMessage（用户 + 助手），使
    前端历史还原逻辑（buildDetectionFromHistory）无需区分来源。

    与 /stream 一致：用户原始附件和检测结果都上传 MinIO，数据库只保存
    object_name；tool_result 只存剥离 base64 的统计信息。失败不抛出，以免
    影响检测结果返回。
    """
    if not session_uuid:
        return
    tool_results = [{"tool": tool_name, "result": json.dumps(result, ensure_ascii=False)}]
    user_attachment_refs = _upload_user_attachment_refs(user_id, original_attachments)
    db = SessionLocal()
    try:
        db_session = _get_or_create_session(db, user_id, session_uuid, user_label)
        db.add(ChatMessage(
            session_id=db_session.id,
            role="user",
            content=f"[快捷检测] {user_label}",
            attachments=user_attachment_refs or None,
        ))
        attachment_refs = _extract_attachment_refs(tool_results)
        slim_tool_results = [
            {"tool": tr.get("tool"), "result": _slim_tool_result(tr.get("result"))}
            for tr in tool_results
        ]
        total = result.get("total_objects", 0)
        db.add(ChatMessage(
            session_id=db_session.id,
            role="assistant",
            content=f"检测完成，共发现 {total} 个目标。",
            agent_used="detection",
            tool_calls=[{"tool": tool_name}],
            tool_result=json.dumps(slim_tool_results, ensure_ascii=False),
            attachments=attachment_refs or None,
        ))
        db_session.message_count = int(db_session.message_count or 0) + 2
        db_session.last_message_at = datetime.now()
        db.commit()
    except Exception as e:
        db.rollback()
        _delete_attachment_objects(user_attachment_refs)
        logger.error("快捷检测落库失败 session=%s tool=%s: %s", session_uuid, tool_name, str(e), exc_info=True)
    finally:
        db.close()


def _collect_object_names(attachments: list[dict] | None) -> list[str]:
    """
    从存储的 attachments 引用中收集所有 MinIO 永久对象名。

    覆盖单图（object_name）、视频（object_name）与批量（images[].object_name）
    三种结构，用于删除会话时清理对应的对象存储文件。
    """
    if not attachments:
        return []
    names = []
    for ref in attachments:
        if ref.get("type") == "images":
            for img in ref.get("images", []):
                name = img.get("object_name")
                if name:
                    names.append(name)
        else:
            name = ref.get("object_name")
            if name:
                names.append(name)
    return names


def _delete_attachment_objects(attachments: list[dict] | None) -> None:
    """尽力删除一组附件引用对应的 MinIO 对象，不因单个失败中断。"""
    object_names = set(_collect_object_names(attachments))
    if not object_names:
        return
    try:
        minio = MinIOClient()
    except Exception as e:
        logger.warning("MinIO 不可用，跳过附件对象清理: %s", str(e))
        return
    for object_name in object_names:
        try:
            minio.delete_file(object_name)
        except Exception as e:
            logger.warning("删除 MinIO 对象失败 object_name=%s: %s", object_name, str(e))


def _delete_session_objects(messages) -> None:
    """
    删除会话所有消息在 MinIO 中引用的原始附件及标注结果对象。

    对象名均为上传时生成的唯一路径，可安全删除，不会影响其他会话。
    MinIO 不可用或单个对象删除失败时记录告警并继续，不阻断会话删除。
    """
    attachments = []
    for message in messages:
        attachments.extend(message.attachments or [])
    _delete_attachment_objects(attachments)


def _resign_attachments(attachments: list[dict] | None) -> list[dict]:
    """
    读取历史时把存储的 object_name 实时换签为短期访问 URL。

    对象已被删除或 MinIO 不可用时，对应项的 url 为 None，前端据此回退提示。
    """
    if not attachments:
        return []
    try:
        minio = MinIOClient()
    except Exception as e:
        logger.warning("MinIO 不可用，历史附件无法换签: %s", str(e))
        return []

    resolved = []
    for ref in attachments:
        ref_type = ref.get("type")
        # 保留 source/filename/content_type/size 等元数据，只移除永久 object_name。
        metadata = {
            key: value
            for key, value in ref.items()
            if key not in {"object_name", "images"}
        }
        if ref_type == "images":
            images = []
            for img in ref.get("images", []):
                image_metadata = {
                    key: value for key, value in img.items() if key != "object_name"
                }
                image_metadata["url"] = minio.presign_from_url_or_name(
                    img.get("object_name", "")
                )
                images.append(image_metadata)
            resolved.append({**metadata, "images": images})
        else:
            resolved.append({
                **metadata,
                "url": minio.presign_from_url_or_name(ref.get("object_name", "")),
            })
    return resolved


@router.get("/sessions", summary="获取当前用户会话列表")
def list_sessions(current_user=Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    sessions = db.query(ChatSession).filter(
        ChatSession.user_id == current_user.id,
        ChatSession.status == "active",
    ).order_by(ChatSession.last_message_at.desc(), ChatSession.created_at.desc()).all()
    return [
        {
            "id": item.id,
            "session_uuid": item.session_uuid,
            "title": item.title,
            "message_count": item.message_count,
            "last_message_at": item.last_message_at,
            "created_at": item.created_at,
        }
        for item in sessions
    ]


@router.get("/sessions/{session_uuid}", summary="获取会话历史")
def get_session_history(session_uuid: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    session = db.query(ChatSession).filter(ChatSession.user_id == current_user.id, ChatSession.session_uuid == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session": {"session_uuid": session.session_uuid, "title": session.title, "message_count": session.message_count},
        "messages": [
            {
                "role": item.role,
                "content": item.content,
                "agent_used": item.agent_used,
                "tool_calls": item.tool_calls,
                "tool_result": item.tool_result,
                # 读取时用存储的 object_name 实时换签短期 URL，避免存过期链接
                "attachments": _resign_attachments(item.attachments),
                "created_at": item.created_at,
            }
            for item in session.messages
        ],
    }


@router.delete("/sessions/{session_uuid}", summary="删除会话")
def delete_session(session_uuid: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    session = db.query(ChatSession).filter(ChatSession.user_id == current_user.id, ChatSession.session_uuid == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    attachments = conversation_memory.load_all_attachments(session_uuid, current_user.id)
    _cleanup_attachments(attachments)
    # 清理 MinIO 中该会话引用的标注图/视频对象（须在 db.delete 前读取消息）。
    _delete_session_objects(session.messages)
    conversation_memory.clear(session_uuid, current_user.id)
    db.delete(session)
    db.commit()
    return {"message": "会话已删除"}


@router.post("/upload", summary="上传聊天检测附件")
async def upload_attachments(
    files: list[UploadFile] = File(...),
    current_user=Depends(get_current_user),
):
    """
    上传图片、ZIP 或视频到服务端临时目录。

    Returns:
        { "attachments": [{"type": "image", "path": "...", ...}] }
    """
    upload_files = list(files)
    if not upload_files:
        raise HTTPException(status_code=400, detail="请选择要上传的附件")

    file_types = [_attachment_type(item.filename or "") for item in upload_files]
    if any(item is None for item in file_types):
        raise HTTPException(
            status_code=400,
            detail="仅支持图片、ZIP 和视频文件",
        )
    if len(set(file_types)) > 1:
        raise HTTPException(status_code=400, detail="一次不能混合上传不同类型的附件")
    if file_types[0] in {"zip", "video"} and len(upload_files) > 1:
        raise HTTPException(status_code=400, detail="ZIP 或视频附件一次只能上传一个")
    if file_types[0] == "image" and len(upload_files) > 20:
        raise HTTPException(status_code=400, detail="批量图片一次最多上传 20 张")

    attachments = []
    saved_paths = []
    try:
        for upload_file, attachment_type in zip(upload_files, file_types):
            original_name = Path(upload_file.filename or "upload").name
            content = await upload_file.read()
            max_size = MAX_UPLOAD_SIZES[attachment_type]
            if len(content) > max_size:
                raise HTTPException(
                    status_code=413,
                    detail=f"{original_name} 超过大小限制",
                )

            stored_name = f"{uuid.uuid4().hex}_{original_name}"
            file_path = os.path.abspath(os.path.join(UPLOAD_DIR, stored_name))
            with open(file_path, "wb") as output:
                output.write(content)
            saved_paths.append(file_path)
            attachments.append(
                {
                    "type": attachment_type,
                    "path": file_path,
                    "filename": original_name,
                }
            )
    except Exception:
        for saved_path in saved_paths:
            try:
                os.unlink(saved_path)
            except OSError:
                pass
        raise

    logger.info(
        "聊天附件上传成功: 用户=%s, 类型=%s, 数量=%d",
        current_user.username,
        file_types[0],
        len(attachments),
    )
    response = {"attachments": attachments}
    # 兼容旧版单图调用方。
    if len(attachments) == 1 and attachments[0]["type"] == "image":
        response["image_path"] = attachments[0]["path"]
    return response


@router.post("/stream")
async def chat_stream(
    request: Request,
    current_user=Depends(get_current_user),
):
    """
    SSE 流式对话接口

    请求体：
    {
        "message": "检测这张图片",
        "attachments": [{"type": "image", "path": "..."}], // 可选
        "session_id": 123                        // 可选，会话 ID
    }

    响应：SSE 流式事件
    """
    # ── 解析请求体 ──
    body = await request.json()
    message = body.get("message", "")
    raw_attachments = body.get("attachments") or []
    # 兼容旧版 image_path 请求体。
    if body.get("image_path"):
        raw_attachments.append(
            {
                "type": "image",
                "path": body["image_path"],
                "filename": os.path.basename(body["image_path"]),
            }
        )
    attachments = _normalize_attachments(raw_attachments)
    requested_session_id = body.get("session_id")
    session_id = str(requested_session_id or uuid.uuid4())

    if not message and not attachments:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    if not message:
        message = "请检测我上传的附件"

    logger.info(
        "用户 %s 发起对话: message=%s, attachments=%s",
        current_user.username,
        message[:50],
        [item["type"] for item in attachments],
    )

    # ── SSE 流式响应 ──
    async def event_generator():
        db = SessionLocal()
        db_session = None
        assistant_chunks = []
        tool_calls = []
        tool_results = []
        agent_used = None
        user_attachment_refs = []
        user_message_committed = False
        try:
            db_session = _get_or_create_session(db, current_user.id, session_id, message)
            if not conversation_memory.load(session_id, current_user.id):
                for previous in db_session.messages[-conversation_memory.max_messages:]:
                    conversation_memory.append(
                        session_id,
                        previous.role,
                        previous.content,
                        current_user.id,
                    )
            user_attachment_refs = await asyncio.to_thread(
                _upload_user_attachment_refs,
                current_user.id,
                attachments,
            )
            db.add(ChatMessage(
                session_id=db_session.id,
                role="user",
                content=message,
                attachments=user_attachment_refs or None,
            ))
            db_session.message_count = int(db_session.message_count or 0) + 1
            db_session.last_message_at = datetime.now()
            db.commit()
            user_message_committed = True
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'thinking', 'content': '正在分析您的请求…'}, ensure_ascii=False)}\n\n"
            # 使用 Agent 流式处理（透传当前用户，用于检测记录归属）
            # 注：scene_id 不传，由检测服务自动选取默认场景；
            # session_id 是会话 ID，与检测场景无关，不可混用。
            async for event in detection_agent.chat_stream(
                message=message,
                attachments=attachments,
                user_id=current_user.id,
                session_id=session_id,
            ):
                if event.get("type") == "text_chunk":
                    assistant_chunks.append(str(event.get("content", "")))
                elif event.get("type") == "tool_call":
                    tool_calls.append({"tool": event.get("tool"), "input": event.get("input")})
                elif event.get("type") == "tool_result":
                    # 保留完整结果（含 base64/URL）供后续提取 MinIO 引用，
                    # 落库前再由 _slim_tool_result 剥离 base64。
                    tool_results.append({"tool": event.get("tool"), "result": str(event.get("result", ""))})
                elif event.get("type") == "agent_route":
                    agent_used = event.get("agent")
                # 将事件序列化为 SSE 格式
                event_data = json.dumps(event, ensure_ascii=False)
                yield f"data: {event_data}\n\n"

            # 标准完成事件和兼容旧客户端的流结束标志
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

            assistant_content = "".join(assistant_chunks).strip()
            if assistant_content or tool_results:
                # 提取标注图/视频的 MinIO 永久引用，供历史还原（不落 base64）。
                attachment_refs = _extract_attachment_refs(tool_results)
                # 存库前剥离 base64：base64 只服务本轮前端渲染，存进数据库会撑爆
                # 字段、拖慢历史加载，且历史已改用 MinIO URL 还原。
                slim_tool_results = [
                    {"tool": tr.get("tool"), "result": _slim_tool_result(tr.get("result"))}
                    for tr in tool_results
                ]
                db.add(ChatMessage(
                    session_id=db_session.id,
                    role="assistant",
                    content=assistant_content or "工具调用已完成",
                    agent_used=agent_used,
                    tool_calls=tool_calls or None,
                    tool_result=json.dumps(slim_tool_results, ensure_ascii=False) if slim_tool_results else None,
                    attachments=attachment_refs or None,
                ))
                db_session.message_count = int(db_session.message_count or 0) + 1
                db_session.last_message_at = datetime.now()
                db.commit()
        except Exception as e:
            db.rollback()
            if not user_message_committed:
                _delete_attachment_objects(user_attachment_refs)
            logger.error("SSE 流异常: %s", str(e), exc_info=True)
            error_data = json.dumps(
                {"type": "error", "content": str(e)},
                ensure_ascii=False,
            )
            yield f"data: {error_data}\n\n"
        finally:
            db.close()
            # 有会话的附件路径会进入对话记忆，需保留到会话过期，才能支持
            # “再检测一次”。无会话的旧客户端仍在本轮结束后立即清理。
            if not requested_session_id:
                _cleanup_attachments(attachments)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁止 Nginx 缓冲 SSE
        },
    )
