"""Same-origin streaming proxy for private MinIO browser assets."""

import mimetypes
import re
from collections.abc import Iterator
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from minio.error import S3Error

from app.services.file_access_service import decode_file_access_token
from app.storage.minio_client import MinIOClient


router = APIRouter(prefix="/api/files", tags=["文件访问"])
RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
CHUNK_SIZE = 64 * 1024


def _parse_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    """Parse one HTTP byte range and return inclusive start/end offsets."""
    if not range_header:
        return None
    match = RANGE_PATTERN.fullmatch(range_header.strip())
    if not match or size <= 0:
        raise ValueError("无效的 Range 请求")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("无效的 Range 请求")
    if not start_text:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError("无效的 Range 请求")
        start = max(size - suffix_length, 0)
        end = size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
        if start >= size:
            raise ValueError("Range 超出文件大小")
        end = min(end, size - 1)
        if end < start:
            raise ValueError("无效的 Range 请求")
    return start, end


def _stream_minio_response(response) -> Iterator[bytes]:
    """Yield MinIO response bytes and always release the HTTP connection."""
    try:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        response.close()
        response.release_conn()


@router.get("/{file_token}", summary="读取私有文件")
def read_private_file(file_token: str, request: Request) -> StreamingResponse:
    """Validate a short-lived token and stream the corresponding MinIO object."""
    try:
        payload = decode_file_access_token(file_token)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    minio = MinIOClient(ensure_bucket=False)
    object_name = payload["object_name"]
    try:
        item = minio.client.stat_object(minio.bucket_name, object_name)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            raise HTTPException(status_code=404, detail="文件不存在") from exc
        raise HTTPException(status_code=502, detail="文件存储服务读取失败") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="文件存储服务不可用") from exc

    size = int(item.size)
    try:
        requested_range = _parse_range(request.headers.get("range"), size)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail=str(exc),
            headers={"Content-Range": f"bytes */{size}"},
        ) from exc

    start, end = requested_range or (0, max(size - 1, 0))
    length = end - start + 1 if size else 0
    try:
        response = minio.client.get_object(
            minio.bucket_name,
            object_name,
            offset=start,
            length=length,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="文件存储服务读取失败") from exc

    filename = payload["filename"]
    content_type = (
        payload["content_type"]
        or getattr(item, "content_type", None)
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "private, max-age=300",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        "X-Content-Type-Options": "nosniff",
    }
    response_status = status.HTTP_200_OK
    if requested_range is not None:
        response_status = status.HTTP_206_PARTIAL_CONTENT
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    return StreamingResponse(
        _stream_minio_response(response),
        status_code=response_status,
        media_type=content_type,
        headers=headers,
    )
