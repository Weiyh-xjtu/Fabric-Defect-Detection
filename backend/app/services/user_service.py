"""用户注册、认证、资料与角色查询服务。"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.core.logger import get_logger
from app.core.rbac import QUALITY_INSPECTOR, SYSTEM_ADMIN, get_user_permission_codes
from app.entity.db_models import Role, RolePermission, User, UserRole

logger = get_logger(__name__)


class UserService:
    """用户服务"""

    @staticmethod
    def register(db: Session, username: str, email: str, password: str) -> User:
        """
        用户注册

        Args:
            db: 数据库会话
            username: 用户名
            email: 邮箱
            password: 明文密码

        Returns:
            新创建的用户对象

        Raises:
            HTTPException: 用户名或邮箱已存在
        """
        # 检查用户名是否已存在
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="用户名已存在")

        # 检查邮箱是否已存在
        existing_email = db.query(User).filter(User.email == email).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="邮箱已被注册")

        default_role = db.query(Role).filter(Role.name == QUALITY_INSPECTOR).first()
        if default_role is None:
            raise HTTPException(status_code=503, detail="系统角色尚未初始化，请联系管理员")

        # 创建新用户并在同一事务中分配默认质检角色。
        new_user = User(
            username=username,
            email=email,
            hashed_password=hash_password(password),
        )
        db.add(new_user)
        db.flush()
        db.add(UserRole(user_id=new_user.id, role_id=default_role.id))
        db.commit()
        db.refresh(new_user)
        return new_user

    @staticmethod
    def login(db: Session, username: str, password: str) -> User:
        """
        用户登录

        Args:
            db: 数据库会话
            username: 用户名
            password: 明文密码

        Returns:
            登录成功的用户对象

        Raises:
            HTTPException: 用户名或密码错误
        """
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if not verify_password(password, user.hashed_password):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if not user.is_active:
            raise HTTPException(status_code=403, detail="账号已被禁用")
        return user

    @staticmethod
    def create_access_token_for_user(user: User) -> str:
        """为用户生成 JWT Token"""
        return create_access_token(data={"sub": str(user.id)})

    @staticmethod
    def create_refresh_token_for_user(user: User) -> str:
        """为用户生成用于滑动续期的 Refresh Token。"""
        return create_refresh_token(data={"sub": str(user.id)})

    @staticmethod
    def get_user_roles(db: Session, user: User) -> list[str]:
        """获取用户的角色标识列表"""
        return [ur.role.name for ur in user.user_roles]

    @staticmethod
    def get_user_permissions(db: Session, user: User) -> list[str]:
        """获取用户所有角色权限的并集。"""
        return sorted(get_user_permission_codes(db, user))

    @staticmethod
    def get_user_by_id(db: Session, user_id: int) -> User:
        """根据 ID 获取用户"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        return user

    @staticmethod
    def list_users(
        db: Session,
        page: int = 1,
        page_size: int = 20,
        keyword: Optional[str] = None,
    ) -> dict:
        """分页查询用户，并返回其角色列表。"""
        query = db.query(User).options(
            selectinload(User.user_roles).joinedload(UserRole.role)
        )
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            query = query.filter(
                (User.username.ilike(pattern)) | (User.email.ilike(pattern))
            )
        total = query.count()
        users = (
            query.order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "items": [UserService._serialize_user(user, db) for user in users],
        }

    @staticmethod
    def _serialize_user(user: User, db: Session | None = None) -> dict:
        """序列化用户信息，排除密码等敏感字段。"""
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "phone": user.phone,
            "avatar": user.avatar,
            "is_active": bool(user.is_active),
            "is_superuser": bool(user.is_superuser),
            "roles": [user_role.role.name for user_role in user.user_roles],
            "permissions": sorted(get_user_permission_codes(db, user)) if db else [],
            "last_login_at": (
                user.last_login_at.isoformat() if user.last_login_at else None
            ),
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }

    @staticmethod
    def get_user_detail(db: Session, user_id: int) -> dict:
        """查询用户详情及角色。"""
        user = (
            db.query(User)
            .options(selectinload(User.user_roles).joinedload(UserRole.role))
            .filter(User.id == user_id)
            .first()
        )
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        return UserService._serialize_user(user, db)

    @staticmethod
    def list_roles(db: Session) -> list[dict]:
        """查询角色及其权限编码。"""
        roles = (
            db.query(Role)
            .options(
                selectinload(Role.role_permissions).joinedload(
                    RolePermission.permission
                )
            )
            .order_by(Role.id)
            .all()
        )
        return [
            {
                "id": role.id,
                "name": role.name,
                "display_name": role.display_name,
                "description": role.description,
                "is_system": bool(role.is_system),
                "permissions": [
                    item.permission.code for item in role.role_permissions
                ],
            }
            for role in roles
        ]

    @staticmethod
    def _active_admin_count(db: Session, exclude_user_id: int | None = None) -> int:
        query = (
            db.query(User.id)
            .outerjoin(UserRole, UserRole.user_id == User.id)
            .outerjoin(Role, Role.id == UserRole.role_id)
            .filter(
                User.is_active.is_(True),
                or_(User.is_superuser.is_(True), Role.name == SYSTEM_ADMIN),
            )
            .distinct()
        )
        if exclude_user_id is not None:
            query = query.filter(User.id != exclude_user_id)
        return query.count()

    @staticmethod
    def replace_user_roles(
        db: Session,
        user_id: int,
        role_names: list[str],
    ) -> dict:
        """整体替换用户角色，并防止移除最后一个有效管理员。"""
        normalized_names = list(dict.fromkeys(name.strip() for name in role_names if name.strip()))
        if not normalized_names:
            raise HTTPException(status_code=400, detail="至少需要分配一个有效角色")
        roles = db.query(Role).filter(Role.name.in_(normalized_names)).all()
        found_names = {role.name for role in roles}
        missing = sorted(set(normalized_names) - found_names)
        if missing:
            raise HTTPException(status_code=400, detail=f"角色不存在: {', '.join(missing)}")

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        current_admin = user.is_superuser or any(
            item.role.name == SYSTEM_ADMIN for item in user.user_roles
        )
        remains_admin = user.is_superuser or SYSTEM_ADMIN in found_names
        if (
            user.is_active
            and current_admin
            and not remains_admin
            and UserService._active_admin_count(db, exclude_user_id=user.id) == 0
        ):
            raise HTTPException(status_code=400, detail="不能移除系统中最后一个有效管理员")

        db.query(UserRole).filter(UserRole.user_id == user.id).delete(
            synchronize_session=False
        )
        for role in roles:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return UserService.get_user_detail(db, user.id)

    @staticmethod
    def update_user_status(
        db: Session,
        user_id: int,
        is_active: bool,
        operator_id: int,
    ) -> dict:
        """启用或禁用用户，并保护当前及最后一个管理员账号。"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if not is_active and user.id == operator_id:
            raise HTTPException(status_code=400, detail="不能禁用当前登录账号")
        is_admin = user.is_superuser or any(
            item.role.name == SYSTEM_ADMIN for item in user.user_roles
        )
        if (
            not is_active
            and user.is_active
            and is_admin
            and UserService._active_admin_count(db, exclude_user_id=user.id) == 0
        ):
            raise HTTPException(status_code=400, detail="不能禁用系统中最后一个有效管理员")
        user.is_active = is_active
        db.commit()
        return UserService.get_user_detail(db, user.id)

    @staticmethod
    def update_profile(
        db: Session,
        user_id: int,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> dict:
        """更新当前用户的手机号和邮箱。"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if email is not None:
            normalized_email = email.strip()
            if not normalized_email:
                raise HTTPException(status_code=400, detail="邮箱不能为空")
            duplicate = (
                db.query(User)
                .filter(User.email == normalized_email, User.id != user_id)
                .first()
            )
            if duplicate:
                raise HTTPException(status_code=400, detail="该邮箱已被其他用户使用")
            user.email = normalized_email
        if phone is not None:
            user.phone = phone.strip() or None
        db.commit()
        db.refresh(user)
        logger.info("用户 %s 更新了个人信息", user.username)
        return {
            "message": "个人信息已更新",
            "user": UserService._serialize_user(user, db),
        }

    @staticmethod
    def change_password(
        db: Session,
        user_id: int,
        old_password: str,
        new_password: str,
    ) -> dict:
        """验证旧密码后更新当前用户密码。"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if not verify_password(old_password, user.hashed_password):
            raise HTTPException(status_code=400, detail="旧密码不正确")
        if verify_password(new_password, user.hashed_password):
            raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")
        user.hashed_password = hash_password(new_password)
        db.commit()
        logger.info("用户 %s 修改了密码", user.username)
        return {"message": "密码修改成功"}


# 全局单例
user_service = UserService()
