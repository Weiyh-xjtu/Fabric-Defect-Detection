"""Same-origin MinIO file proxy tests."""

import io
from types import SimpleNamespace

import pytest

import app.api.files as files_api
from app.services.file_access_service import (
    create_file_access_url,
    decode_file_access_token,
)
from app.storage.minio_client import MinIOClient


FILE_BYTES = b"0123456789"


class _ObjectResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.released = False

    def release_conn(self) -> None:
        self.released = True


class _FakeMinioApi:
    requested: list[tuple[int, int]] = []

    def stat_object(self, bucket_name: str, object_name: str):
        assert bucket_name == "test-bucket"
        assert object_name == "avatars/7/avatar.jpg"
        return SimpleNamespace(size=len(FILE_BYTES), content_type="image/jpeg")

    def get_object(
        self,
        bucket_name: str,
        object_name: str,
        *,
        offset: int = 0,
        length: int = 0,
    ) -> _ObjectResponse:
        assert bucket_name == "test-bucket"
        assert object_name == "avatars/7/avatar.jpg"
        self.requested.append((offset, length))
        end = offset + length if length else None
        return _ObjectResponse(FILE_BYTES[offset:end])


class _FakeMinioClient:
    bucket_name = "test-bucket"
    client = _FakeMinioApi()

    def __init__(self, ensure_bucket: bool = True):
        self.ensure_bucket = ensure_bucket


def test_file_access_token_roundtrip_and_proxy_url_decode():
    url = create_file_access_url(
        "avatars/7/avatar.jpg",
        filename="头像.jpg",
        content_type="image/jpeg",
    )
    token = url.rsplit("/", 1)[-1]
    payload = decode_file_access_token(token)

    assert payload == {
        "object_name": "avatars/7/avatar.jpg",
        "filename": "头像.jpg",
        "content_type": "image/jpeg",
    }

    client = object.__new__(MinIOClient)
    client.bucket_name = "test-bucket"
    assert client.object_name_from_url(url) == "avatars/7/avatar.jpg"


def test_file_access_rejects_non_browser_object_prefix():
    with pytest.raises(ValueError):
        create_file_access_url("models/private.pt")


def test_file_proxy_streams_full_object(client, monkeypatch):
    _FakeMinioApi.requested.clear()
    monkeypatch.setattr(files_api, "MinIOClient", _FakeMinioClient)
    url = create_file_access_url(
        "avatars/7/avatar.jpg",
        filename="avatar.jpg",
        content_type="image/jpeg",
    )

    response = client.get(url)

    assert response.status_code == 200
    assert response.content == FILE_BYTES
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(FILE_BYTES))
    assert _FakeMinioApi.requested == [(0, len(FILE_BYTES))]


def test_file_proxy_supports_video_style_range_requests(client, monkeypatch):
    _FakeMinioApi.requested.clear()
    monkeypatch.setattr(files_api, "MinIOClient", _FakeMinioClient)
    url = create_file_access_url("avatars/7/avatar.jpg")

    response = client.get(url, headers={"Range": "bytes=2-5"})

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["content-length"] == "4"
    assert _FakeMinioApi.requested == [(2, 4)]


def test_file_proxy_rejects_invalid_token(client):
    response = client.get("/api/files/not-a-valid-token")
    assert response.status_code == 404
