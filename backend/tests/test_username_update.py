"""当前用户修改用户名的行为测试。"""
from app.entity.db_models import User


def _register(client, username: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "123456",
        },
    )
    assert response.status_code == 201
    return response.json()


def _login_headers(client, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "123456"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_username_can_be_changed_but_cannot_duplicate_another_user(
    client,
    db_session,
):
    original = _register(client, "rename_original")
    _register(client, "rename_existing")
    headers = _login_headers(client, "rename_original")

    duplicate = client.put(
        "/api/user/profile",
        params={"username": "rename_existing"},
        headers=headers,
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["message"] == "用户名已被其他用户使用"

    renamed = client.put(
        "/api/user/profile",
        params={"username": "  rename_updated  "},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["user"]["id"] == original["id"]
    assert renamed.json()["user"]["username"] == "rename_updated"

    current_user = client.get("/api/auth/me", headers=headers)
    assert current_user.status_code == 200
    assert current_user.json()["id"] == original["id"]
    assert current_user.json()["username"] == "rename_updated"

    old_login = client.post(
        "/api/auth/login",
        json={"username": "rename_original", "password": "123456"},
    )
    assert old_login.status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "rename_updated", "password": "123456"},
    ).status_code == 200

    db_session.expire_all()
    stored = db_session.query(User).filter(User.id == original["id"]).one()
    assert stored.username == "rename_updated"
