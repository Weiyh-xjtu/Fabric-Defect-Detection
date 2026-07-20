"""用户资料、用户列表与角色权限查询 API。"""

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.permissions import require_permission
from app.core.rbac import USER_MANAGE
from app.database.session import get_db
from app.entity.db_models import User
from app.entity.schemas import (
    RoleCreate,
    RolePermissionsUpdate,
    RoleUpdate,
    UserRolesUpdate,
    UserStatusUpdate,
)
from app.services.user_service import AVATAR_MAX_FILE_SIZE, user_service

router = APIRouter(prefix="/api/user", tags=["用户管理"])


@router.get("/list", summary="用户列表")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=100),
    _current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    """分页查询用户，可供管理页或智能体工具使用。"""
    return user_service.list_users(db, page, page_size, keyword)


@router.get("/roles", summary="获取所有角色")
async def list_roles(
    _current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    """返回角色及其权限编码。"""
    return {"roles": user_service.list_roles(db)}


@router.get("/permissions", summary="获取所有权限定义")
async def list_permissions(
    _current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    """返回全部权限编码及其名称、所属模块，供角色权限编辑使用。"""
    return {"permissions": user_service.list_permissions(db)}


@router.put("/roles/{role_id}/permissions", summary="修改角色权限")
async def update_role_permissions(
    role_id: int,
    request: RolePermissionsUpdate,
    current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    """整体替换指定角色的权限集合，变更对该角色下所有用户立即生效。"""
    return user_service.replace_role_permissions(
        db, role_id, request.permission_codes, current_user
    )


@router.post("/roles", summary="创建角色", status_code=201)
async def create_role(
    request: RoleCreate,
    current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    """创建自定义角色，可同时指定初始权限。"""
    return user_service.create_role(
        db,
        request.name,
        request.display_name,
        request.description,
        request.permission_codes,
        current_user,
    )


@router.put("/roles/{role_id}", summary="修改角色信息")
async def update_role(
    role_id: int,
    request: RoleUpdate,
    current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    """修改角色标识、显示名或描述；系统内置角色的标识不可修改。"""
    return user_service.update_role(
        db,
        role_id,
        request.name,
        request.display_name,
        request.description,
        current_user,
    )


@router.delete("/roles/{role_id}", summary="删除角色")
async def delete_role(
    role_id: int,
    current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    """删除自定义角色并解除其用户关联；系统内置角色不可删除。"""
    return user_service.delete_role(db, role_id, current_user)


@router.put("/profile", summary="更新个人信息")
async def update_profile(
    username: str | None = Query(None, min_length=3, max_length=50),
    phone: str | None = Query(None, max_length=20),
    email: str | None = Query(None, max_length=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """更新当前用户的用户名、手机号和邮箱。"""
    return user_service.update_profile(
        db,
        current_user.id,
        phone=phone,
        email=email,
        username=username,
    )


@router.put("/avatar", summary="上传或替换头像")
async def update_avatar(
    file: UploadFile = File(..., description="JPG、PNG 或 WebP 头像图片，最大 5 MB"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """校验、裁剪并上传当前用户头像。"""
    image_data = await file.read(AVATAR_MAX_FILE_SIZE + 1)
    return user_service.update_avatar(
        db,
        current_user.id,
        image_data,
        file.content_type,
    )


@router.delete("/avatar", summary="恢复默认头像")
async def remove_avatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """移除自定义头像，恢复显示用户名首字母。"""
    return user_service.remove_avatar(db, current_user.id)


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
    _current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    """查询指定用户的非敏感资料。"""
    return user_service.get_user_detail(db, user_id)


@router.put("/{user_id}/roles", summary="修改用户角色")
async def update_user_roles(
    user_id: int,
    request: UserRolesUpdate,
    _current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    return user_service.replace_user_roles(db, user_id, request.role_names)


@router.put("/{user_id}/status", summary="启用或禁用用户")
async def update_user_status(
    user_id: int,
    request: UserStatusUpdate,
    current_user: User = Depends(require_permission(USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    return user_service.update_user_status(
        db, user_id, request.is_active, current_user.id
    )
