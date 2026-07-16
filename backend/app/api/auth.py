"""
认证相关 API 路由
- POST /api/auth/register  用户注册
- POST /api/auth/login     用户登录
- POST /api/auth/refresh   刷新登录会话
- POST /api/auth/logout    清除刷新 Cookie
- GET  /api/auth/me        获取当前用户信息
"""
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.security import decode_access_token, decode_refresh_token
from app.database.session import get_db
from app.entity.db_models import User
from app.entity.schemas import TokenResponse, UserLogin, UserRegister, UserResponse
from app.services.user_service import user_service

router = APIRouter(prefix="/api/auth", tags=["认证"])

# Bearer Token 方案，用于从请求 Header 中提取 Token
# Swagger UI 的 Authorize 弹窗可直接粘贴 Token（不需要加 Bearer 前缀）
# auto_error=False：缺少 Token 时不抛默认的 403，由我们统一返回 401
bearer_scheme = HTTPBearer(auto_error=False)


def _build_token_response(user: User, db: Session) -> dict:
    """构造登录或续期成功后的统一响应。"""
    access_token = user_service.create_access_token_for_user(user)
    roles = user_service.get_user_roles(db, user)
    permissions = user_service.get_user_permissions(db, user)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "avatar": user.avatar,
            "roles": roles,
            "permissions": permissions,
            "is_superuser": bool(user.is_superuser),
        },
    }


def _set_refresh_cookie(response: Response, user: User) -> None:
    """签发短期 HttpOnly Refresh Cookie，用于滑动续期。"""
    refresh_token = user_service.create_refresh_token_for_user(user)
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite="lax",
        path="/api/auth",
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    """
    从 JWT Token 中解析当前用户

    在需要认证的路由中通过 Depends(get_current_user) 使用
    """
    credentials_exception = HTTPException(
        status_code=401,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise credentials_exception
    try:
        token = credentials.credentials
        payload = decode_access_token(token)
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception

    user = user_service.get_user_by_id(db, user_id)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return user


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(request: UserRegister, db: Session = Depends(get_db)):
    """
    用户注册

    - **username**: 用户名（3-50 字符）
    - **email**: 邮箱
    - **password**: 密码（至少 6 位）
    """
    user = user_service.register(
        db=db,
        username=request.username,
        email=request.email,
        password=request.password,
    )
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    request: UserLogin,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    用户登录

    - 返回 JWT access_token
    - 后续请求在 Header 中携带：Authorization: Bearer <token>
    """
    user = user_service.login(
        db=db,
        username=request.username,
        password=request.password,
    )

    _set_refresh_cookie(response, user)
    return _build_token_response(user, db)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_login_session(
    response: Response,
    refresh_token: str | None = Cookie(
        default=None,
        alias=settings.REFRESH_COOKIE_NAME,
    ),
    db: Session = Depends(get_db),
):
    """轮换 Refresh Cookie，并签发新的 Access Token。"""
    credentials_exception = HTTPException(
        status_code=401,
        detail="登录会话已失效，请重新登录",
    )
    if not refresh_token:
        raise credentials_exception

    try:
        payload = decode_refresh_token(refresh_token)
        user_id_str = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user = user_service.get_user_by_id(db, int(user_id_str))
    except (JWTError, ValueError, HTTPException):
        raise credentials_exception

    if not user.is_active:
        raise credentials_exception

    _set_refresh_cookie(response, user)
    return _build_token_response(user, db)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    """清除浏览器中的 Refresh Cookie。"""
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        path="/api/auth",
        secure=settings.REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前登录用户信息（需要 Token 认证）"""
    roles = user_service.get_user_roles(db, current_user)
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "phone": current_user.phone,
        "avatar": current_user.avatar,
        "is_active": current_user.is_active,
        "is_superuser": current_user.is_superuser,
        "roles": roles,
        "permissions": user_service.get_user_permissions(db, current_user),
        "last_login_at": current_user.last_login_at,
        "created_at": current_user.created_at,
    }
