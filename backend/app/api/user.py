"""用户资料、用户列表与角色权限查询 API。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database.session import get_db
from app.entity.db_models import User
from app.services.user_service import user_service

router = APIRouter(prefix="/api/user", tags=["用户管理"])


@router.get("/list", summary="用户列表")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=100),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """分页查询用户，可供管理页或智能体工具使用。"""
    return user_service.list_users(db, page, page_size, keyword)


@router.get("/roles", summary="获取所有角色")
async def list_roles(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """返回角色及其权限编码。"""
    return {"roles": user_service.list_roles(db)}


@router.put("/profile", summary="更新个人信息")
async def update_profile(
    phone: str | None = Query(None, max_length=20),
    email: str | None = Query(None, max_length=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """更新当前用户的手机号和邮箱。"""
    return user_service.update_profile(db, current_user.id, phone, email)


@router.put("/password", summary="修改密码")
async def change_password(
    old_password: str = Query(..., min_length=1, max_length=100),
    new_password: str = Query(..., min_length=6, max_length=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """验证旧密码后修改当前用户密码。"""
    return user_service.change_password(
        db, current_user.id, old_password, new_password
    )


@router.get("/{user_id}", summary="用户详情")
async def get_user_detail(
    user_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """查询指定用户的非敏感资料。"""
    return user_service.get_user_detail(db, user_id)
