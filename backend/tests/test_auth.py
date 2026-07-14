"""
认证接口测试

测试目标：
  - 用户注册：正常注册、重复用户名、参数验证
  - 用户登录：正常登录、错误密码、不存在的用户
  - 获取当前用户：有 Token、无 Token、无效 Token

测试策略：
  - 每个测试用例独立，不依赖其他测试的执行顺序
  - 使用唯一的用户名避免测试间冲突
"""

import pytest

from app.config.settings import settings


class TestRegister:
    """用户注册测试"""

    def test_register_success(self, client):
        """正常注册"""
        response = client.post(
            "/api/auth/register",
            json={
                "username": "test_register_user",
                "email": "test_register@example.com",
                "password": "123456",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "test_register_user"
        assert data["email"] == "test_register@example.com"
        # 确保不返回密码字段
        assert "hashed_password" not in data
        assert "password" not in data

    def test_register_duplicate_username(self, client):
        """重复用户名注册"""
        # 先注册一个用户
        client.post(
            "/api/auth/register",
            json={
                "username": "dup_user",
                "email": "dup1@example.com",
                "password": "123456",
            },
        )
        # 用相同用户名再注册
        response = client.post(
            "/api/auth/register",
            json={
                "username": "dup_user",
                "email": "dup2@example.com",
                "password": "123456",
            },
        )
        assert response.status_code == 400

    def test_register_short_username(self, client):
        """用户名过短（少于 3 字符）"""
        response = client.post(
            "/api/auth/register",
            json={
                "username": "ab",
                "email": "short@example.com",
                "password": "123456",
            },
        )
        assert response.status_code == 422

    def test_register_short_password(self, client):
        """密码过短（少于 6 位）"""
        response = client.post(
            "/api/auth/register",
            json={
                "username": "short_pwd_user",
                "email": "shortpwd@example.com",
                "password": "123",
            },
        )
        assert response.status_code == 422

    def test_register_missing_fields(self, client):
        """缺少必填字段"""
        response = client.post(
            "/api/auth/register",
            json={"username": "no_email_user"},
        )
        assert response.status_code == 422


class TestLogin:
    """用户登录测试"""

    def test_login_success(self, client):
        """正常登录"""
        # 先注册
        client.post(
            "/api/auth/register",
            json={
                "username": "login_user",
                "email": "login@example.com",
                "password": "123456",
            },
        )
        # 再登录
        response = client.post(
            "/api/auth/login",
            json={
                "username": "login_user",
                "password": "123456",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == "login_user"
        assert settings.REFRESH_COOKIE_NAME in client.cookies
        assert "HttpOnly" in response.headers["set-cookie"]

    def test_login_wrong_password(self, client):
        """密码错误"""
        # 先注册
        client.post(
            "/api/auth/register",
            json={
                "username": "wrong_pwd_user",
                "email": "wrongpwd@example.com",
                "password": "123456",
            },
        )
        # 用错误密码登录
        response = client.post(
            "/api/auth/login",
            json={
                "username": "wrong_pwd_user",
                "password": "wrongpassword",
            },
        )
        assert response.status_code == 401

    def test_login_nonexistent_user(self, client):
        """不存在的用户"""
        response = client.post(
            "/api/auth/login",
            json={
                "username": "no_such_user_12345",
                "password": "123456",
            },
        )
        assert response.status_code == 401


class TestRefreshSession:
    """滑动登录会话测试。"""

    def test_refresh_rotates_tokens(self, client):
        """有效 Refresh Cookie 可以换取新的 Access Token。"""
        client.post(
            "/api/auth/register",
            json={
                "username": "refresh_user",
                "email": "refresh@example.com",
                "password": "123456",
            },
        )
        login_response = client.post(
            "/api/auth/login",
            json={"username": "refresh_user", "password": "123456"},
        )
        old_access_token = login_response.json()["access_token"]
        old_refresh_token = client.cookies.get(settings.REFRESH_COOKIE_NAME)

        refresh_response = client.post("/api/auth/refresh")

        assert refresh_response.status_code == 200
        assert refresh_response.json()["access_token"] != old_access_token
        assert client.cookies.get(settings.REFRESH_COOKIE_NAME) != old_refresh_token

    def test_refresh_without_cookie_returns_401(self, client):
        """缺少 Refresh Cookie 时不能续期。"""
        client.cookies.clear()

        response = client.post("/api/auth/refresh")

        assert response.status_code == 401

    def test_access_and_refresh_tokens_cannot_be_mixed(self, client):
        """Access Token 与 Refresh Token 不能互相替代。"""
        client.post(
            "/api/auth/register",
            json={
                "username": "token_type_user",
                "email": "token_type@example.com",
                "password": "123456",
            },
        )
        login_response = client.post(
            "/api/auth/login",
            json={"username": "token_type_user", "password": "123456"},
        )
        access_token = login_response.json()["access_token"]
        refresh_token = client.cookies.get(settings.REFRESH_COOKIE_NAME)

        me_response = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {refresh_token}"},
        )
        client.cookies.clear()
        client.cookies.set(
            settings.REFRESH_COOKIE_NAME,
            access_token,
            path="/api/auth",
        )
        refresh_response = client.post("/api/auth/refresh")

        assert me_response.status_code == 401
        assert refresh_response.status_code == 401

    def test_logout_clears_refresh_cookie(self, client):
        """主动退出会删除 Refresh Cookie。"""
        client.post(
            "/api/auth/register",
            json={
                "username": "logout_user",
                "email": "logout@example.com",
                "password": "123456",
            },
        )
        client.post(
            "/api/auth/login",
            json={"username": "logout_user", "password": "123456"},
        )

        response = client.post("/api/auth/logout")

        assert response.status_code == 204
        assert settings.REFRESH_COOKIE_NAME not in client.cookies


class TestGetCurrentUser:
    """获取当前用户测试"""

    def test_get_me_with_valid_token(self, client):
        """使用有效 Token 获取用户信息"""
        # 注册并登录
        client.post(
            "/api/auth/register",
            json={
                "username": "me_user",
                "email": "me@example.com",
                "password": "123456",
            },
        )
        login_response = client.post(
            "/api/auth/login",
            json={
                "username": "me_user",
                "password": "123456",
            },
        )
        token = login_response.json()["access_token"]

        # 使用 Token 获取用户信息
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "me_user"
        assert data["email"] == "me@example.com"

    def test_get_me_without_token(self, client):
        """不带 Token 访问受保护接口"""
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    def test_get_me_with_invalid_token(self, client):
        """使用无效 Token"""
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid_token_here"},
        )
        assert response.status_code == 401
