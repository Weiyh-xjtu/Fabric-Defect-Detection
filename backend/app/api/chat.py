"""
对话相关 API 路由

接口列表：
  - POST /api/chat/upload    上传检测附件，返回服务端路径
  - POST /api/chat/stream    SSE 流式对话（核心接口）

"""

import json
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
from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.database.session import SessionLocal, get_db
from app.entity.db_models import ChatMessage, ChatSession
from app.agent.memory import conversation_memory

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
            {"role": item.role, "content": item.content, "agent_used": item.agent_used, "tool_calls": item.tool_calls, "tool_result": item.tool_result, "created_at": item.created_at}
            for item in session.messages
        ],
    }


@router.delete("/sessions/{session_uuid}", summary="删除会话")
def delete_session(session_uuid: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    session = db.query(ChatSession).filter(ChatSession.user_id == current_user.id, ChatSession.session_uuid == session_uuid).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    attachments = conversation_memory.load_attachments(session_uuid, current_user.id)
    _cleanup_attachments(attachments)
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
            db.add(ChatMessage(session_id=db_session.id, role="user", content=message))
            db_session.message_count = int(db_session.message_count or 0) + 1
            db_session.last_message_at = datetime.now()
            db.commit()
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
                    tool_results.append({"tool": event.get("tool"), "result": str(event.get("result", ""))[:10000]})
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
                db.add(ChatMessage(
                    session_id=db_session.id,
                    role="assistant",
                    content=assistant_content or "工具调用已完成",
                    agent_used=agent_used,
                    tool_calls=tool_calls or None,
                    tool_result=json.dumps(tool_results, ensure_ascii=False) if tool_results else None,
                ))
                db_session.message_count = int(db_session.message_count or 0) + 1
                db_session.last_message_at = datetime.now()
                db.commit()
        except Exception as e:
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
