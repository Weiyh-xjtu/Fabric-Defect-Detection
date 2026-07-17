"""Restore persistent chat originals into the local attachment cache."""

import hashlib
import os
import tempfile
from pathlib import Path

from app.agent.memory import conversation_memory
from app.core.logger import get_logger
from app.database.session import SessionLocal
from app.entity.db_models import ChatMessage, ChatSession
from app.storage.minio_client import MinIOClient

logger = get_logger(__name__)

RESTORED_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "rsod_uploads")
SUPPORTED_ATTACHMENT_TYPES = {"image", "zip", "video"}


def _has_available_file(history: list[list[dict]]) -> bool:
    """Return whether the cached history still contains a usable local file."""
    return any(
        os.path.isfile(str(item.get("path", "")))
        for attachments in history
        for item in attachments
        if isinstance(item, dict)
    )


def _restored_path(object_name: str, filename: str) -> str:
    """Build a stable, traversal-safe local cache path for a MinIO object."""
    safe_name = Path(filename).name or "attachment"
    object_hash = hashlib.sha256(object_name.encode("utf-8")).hexdigest()[:16]
    return os.path.abspath(
        os.path.join(RESTORED_UPLOAD_DIR, f"restored_{object_hash}_{safe_name}")
    )


def _load_persistent_rounds(
    session_id: str,
    user_id: int,
) -> list[list[dict]]:
    """Load user-original MinIO references after verifying session ownership."""
    db = SessionLocal()
    try:
        session = (
            db.query(ChatSession)
            .filter(
                ChatSession.session_uuid == session_id,
                ChatSession.user_id == user_id,
            )
            .first()
        )
        if session is None:
            return []
        messages = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.session_id == session.id,
                ChatMessage.role == "user",
            )
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .all()
        )
        rounds = []
        expected_prefix = f"chat-originals/{user_id}/"
        for message in messages:
            attachments = []
            for ref in message.attachments or []:
                if not isinstance(ref, dict) or ref.get("source") != "user":
                    continue
                attachment_type = ref.get("type")
                object_name = str(ref.get("object_name") or "")
                filename = Path(str(ref.get("filename") or "attachment")).name
                if (
                    attachment_type not in SUPPORTED_ATTACHMENT_TYPES
                    or not object_name.startswith(expected_prefix)
                ):
                    continue
                attachments.append(
                    {
                        "type": attachment_type,
                        "filename": filename,
                        "object_name": object_name,
                    }
                )
            if attachments:
                rounds.append(attachments)
        return rounds
    finally:
        db.close()


def ensure_session_attachment_history(
    session_id: str | int | None,
    user_id: int | str | None,
) -> list[list[dict]]:
    """Restore history from DB/MinIO when the Redis/local-path cache is unavailable."""
    history = conversation_memory.load_attachment_history(session_id, user_id)
    if _has_available_file(history) or not session_id or user_id is None:
        return history
    try:
        normalized_user_id = int(user_id)
    except (TypeError, ValueError):
        return history

    persistent_rounds = _load_persistent_rounds(
        str(session_id), normalized_user_id
    )
    if not persistent_rounds:
        return history

    try:
        minio = MinIOClient()
    except Exception as exc:
        logger.warning(
            "历史附件恢复失败，MinIO 不可用: session=%s user=%s error=%s",
            session_id,
            normalized_user_id,
            str(exc),
        )
        return history

    restored_rounds = []
    restored_count = 0
    for attachments in persistent_rounds:
        restored_attachments = []
        for item in attachments:
            local_path = _restored_path(item["object_name"], item["filename"])
            if not os.path.isfile(local_path):
                try:
                    minio.download_file(item["object_name"], local_path)
                except Exception as exc:
                    logger.warning(
                        "历史附件下载失败: session=%s object=%s error=%s",
                        session_id,
                        item["object_name"],
                        str(exc),
                    )
            if os.path.isfile(local_path):
                restored_count += 1
            restored_attachments.append(
                {
                    "type": item["type"],
                    "path": local_path,
                    "filename": item["filename"],
                }
            )
        if restored_attachments:
            restored_rounds.append(restored_attachments)

    conversation_memory.replace_attachment_history(
        session_id, restored_rounds, normalized_user_id
    )
    logger.info(
        "历史会话附件已恢复: session=%s user=%s rounds=%d files=%d",
        session_id,
        normalized_user_id,
        len(restored_rounds),
        restored_count,
    )
    return conversation_memory.load_attachment_history(
        session_id, normalized_user_id
    )
