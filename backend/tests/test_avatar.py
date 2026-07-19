"""用户头像上传、换签与恢复默认头像测试。"""
import io
from urllib.parse import urlparse

from PIL import Image

import app.services.user_service as user_service_module
from app.entity.db_models import User


class FakeMinIOClient:
    uploaded: dict[str, tuple[bytes, str]] = {}
    deleted: list[str] = []

    def __init__(self, ensure_bucket: bool = True):
        self.ensure_bucket = ensure_bucket

    def upload_bytes(
        self,
        object_name: str,
        data: bytes,
        content_type: str = "image/jpeg",
    ) -> str:
        self.uploaded[object_name] = (data, content_type)
        return self.get_presigned_url(object_name)

    def get_presigned_url(self, object_name: str) -> str:
        return f"https://minio.test/fabric/{object_name}?signature=fresh"

    def presign_from_url_or_name(self, value: str) -> str:
        return self.get_presigned_url(value)

    def object_name_from_url(self, url: str) -> str | None:
        prefix = "/fabric/"
        path = urlparse(url).path
        return path.split(prefix, 1)[1] if prefix in path else None

    def delete_file(self, object_name: str) -> None:
        self.deleted.append(object_name)
        self.uploaded.pop(object_name, None)


def _register_and_login(client, username: str) -> dict[str, str]:
    client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "123456",
        },
    )
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "123456"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _png_bytes(size: tuple[int, int] = (800, 600)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, (30, 120, 200, 180)).save(output, format="PNG")
    return output.getvalue()


def test_upload_replace_and_remove_avatar(client, db_session, monkeypatch):
    FakeMinIOClient.uploaded.clear()
    FakeMinIOClient.deleted.clear()
    monkeypatch.setattr(user_service_module, "MinIOClient", FakeMinIOClient)
    headers = _register_and_login(client, "avatar_flow_user")

    first = client.put(
        "/api/user/avatar",
        files={"file": ("portrait.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["user"]["avatar"].startswith(
        "https://minio.test/fabric/avatars/"
    )

    db_session.expire_all()
    user = db_session.query(User).filter_by(username="avatar_flow_user").one()
    first_object_name = user.avatar
    assert first_object_name.startswith(f"avatars/{user.id}/")
    uploaded_data, content_type = FakeMinIOClient.uploaded[first_object_name]
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(uploaded_data)) as normalized:
        assert normalized.format == "JPEG"
        assert normalized.size == (512, 512)

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert first_object_name in me.json()["avatar"]

    second = client.put(
        "/api/user/avatar",
        files={"file": ("replacement.webp", _png_bytes((640, 640)), "image/webp")},
        headers=headers,
    )
    assert second.status_code == 200
    assert first_object_name in FakeMinIOClient.deleted

    db_session.expire_all()
    replacement_object_name = (
        db_session.query(User).filter_by(username="avatar_flow_user").one().avatar
    )
    assert replacement_object_name != first_object_name

    removed = client.delete("/api/user/avatar", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["user"]["avatar"] is None
    assert replacement_object_name in FakeMinIOClient.deleted

    db_session.expire_all()
    assert db_session.query(User).filter_by(username="avatar_flow_user").one().avatar is None


def test_avatar_rejects_invalid_image_content(client, monkeypatch):
    monkeypatch.setattr(user_service_module, "MinIOClient", FakeMinIOClient)
    headers = _register_and_login(client, "avatar_invalid_user")

    response = client.put(
        "/api/user/avatar",
        files={"file": ("fake.png", b"not-an-image", "image/png")},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["message"] == "无法识别或处理该头像图片"
