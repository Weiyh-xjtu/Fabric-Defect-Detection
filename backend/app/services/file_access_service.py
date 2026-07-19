"""Generate and validate short-lived URLs for browser file access."""

from datetime import datetime, timedelta
from pathlib import PurePosixPath

from jose import JWTError, jwt

from app.config.settings import settings


ALLOWED_OBJECT_PREFIXES = ("avatars/", "chat-originals/", "detections/")


def _validate_object_name(object_name: str) -> str:
    """Accept only browser-facing objects and reject traversal-like names."""
    normalized = str(object_name or "").replace("\\", "/").lstrip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or ".." in path.parts
        or not normalized.startswith(ALLOWED_OBJECT_PREFIXES)
    ):
        raise ValueError("文件对象名称无效")
    return normalized


def create_file_access_url(
    object_name: str,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    """Return a same-origin URL containing a short-lived signed file token."""
    normalized = _validate_object_name(object_name)
    expire = datetime.utcnow() + timedelta(
        minutes=settings.FILE_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "type": "file_access",
        "object_name": normalized,
        "exp": expire,
    }
    if filename:
        payload["filename"] = PurePosixPath(str(filename).replace("\\", "/")).name
    if content_type:
        payload["content_type"] = str(content_type)[:200]
    token = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return f"/api/files/{token}"


def decode_file_access_token(token: str) -> dict[str, str]:
    """Validate a file token and return its normalized browser-safe payload."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as exc:
        raise ValueError("文件访问链接无效或已过期") from exc
    if payload.get("type") != "file_access":
        raise ValueError("文件访问令牌类型无效")
    object_name = _validate_object_name(payload.get("object_name", ""))
    return {
        "object_name": object_name,
        "filename": PurePosixPath(
            str(payload.get("filename") or PurePosixPath(object_name).name).replace(
                "\\", "/"
            )
        ).name,
        "content_type": str(payload.get("content_type") or ""),
    }
