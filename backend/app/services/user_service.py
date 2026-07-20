"""用户注册、认证、资料、头像与角色查询服务。"""
import io
import uuid
import warnings
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.core.logger import get_logger
from app.core.rbac import QUALITY_INSPECTOR, SYSTEM_ADMIN, get_user_permission_codes
from app.config.settings import settings
from app.entity.db_models import AuthSession, Role, RolePermission, User, UserRole
from app.storage.minio_client import MinIOClient

logger = get_logger(__name__)

AVATAR_MAX_FILE_SIZE = 5 * 1024 * 1024
AVATAR_MAX_PIXELS = 40_000_000
AVATAR_OUTPUT_SIZE = (512, 512)
ALLOWED_AVATAR_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_AVATAR_FORMATS = {"JPEG", "PNG", "WEBP"}


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

        user.last_login_at = datetime.now()
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def create_auth_session(db: Session, user: User) -> AuthSession:
        """为一次登录创建可独立撤销的服务端认证会话。"""
        now = datetime.now()
        auth_session = AuthSession(
            id=str(uuid.uuid4()),
            user_id=user.id,
            created_at=now,
            last_refreshed_at=now,
            expires_at=now + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
        )
        db.add(auth_session)
        db.commit()
        db.refresh(auth_session)
        return auth_session

    @staticmethod
    def get_active_auth_session(
        db: Session,
        user_id: int,
        session_id: str,
    ) -> AuthSession | None:
        """返回仍有效且属于指定用户的认证会话。"""
        auth_session = (
            db.query(AuthSession)
            .filter(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
            )
            .first()
        )
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= datetime.now()
        ):
            return None
        return auth_session

    @staticmethod
    def refresh_auth_session(db: Session, auth_session: AuthSession) -> None:
        """滑动延长有效认证会话的 Refresh 期限。"""
        now = datetime.now()
        auth_session.last_refreshed_at = now
        auth_session.expires_at = now + timedelta(
            minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES
        )
        db.commit()

    @staticmethod
    def revoke_auth_session(db: Session, user_id: int, session_id: str) -> bool:
        """仅撤销指定用户的一次登录会话。"""
        auth_session = (
            db.query(AuthSession)
            .filter(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .first()
        )
        if auth_session is None:
            return False
        auth_session.revoked_at = datetime.now()
        db.commit()
        return True

    @staticmethod
    def revoke_all_auth_sessions(db: Session, user_id: int) -> int:
        """撤销用户当前所有未撤销的登录会话，事务由调用方提交。"""
        return (
            db.query(AuthSession)
            .filter(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .update(
                {AuthSession.revoked_at: datetime.now()},
                synchronize_session=False,
            )
        )

    @staticmethod
    def create_access_token_for_user(user: User, session_id: str) -> str:
        """为用户生成 JWT Token"""
        return create_access_token(data={"sub": str(user.id), "sid": session_id})

    @staticmethod
    def create_refresh_token_for_user(user: User, session_id: str) -> str:
        """为用户生成用于滑动续期的 Refresh Token。"""
        return create_refresh_token(data={"sub": str(user.id), "sid": session_id})

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
    def resolve_avatar_url(avatar: str | None) -> str | None:
        """把数据库中的永久对象名转换为可访问 URL，并兼容历史外部 URL。"""
        if not avatar:
            return None
        try:
            minio = MinIOClient(ensure_bucket=False)
            if avatar.startswith(("http://", "https://")):
                object_name = minio.object_name_from_url(avatar)
                return (
                    minio.browser_url_from_url_or_name(
                        object_name,
                        filename="avatar.jpg",
                        content_type="image/jpeg",
                    )
                    if object_name
                    else avatar
                )
            return minio.browser_url_from_url_or_name(
                avatar,
                filename="avatar.jpg",
                content_type="image/jpeg",
            )
        except Exception as exc:
            logger.warning("头像代理地址生成失败: %s", str(exc))
            return avatar if avatar.startswith(("http://", "https://")) else None

    @staticmethod
    def _serialize_user(user: User, db: Session | None = None) -> dict:
        """序列化用户信息，排除密码等敏感字段。"""
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "phone": user.phone,
            "avatar": UserService.resolve_avatar_url(user.avatar),
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

        # 通过 ORM 关系删除，确保旧关联同步退出 identity map。批量 DELETE 后
        # SQLite 可能复用主键，导致新对象与会话中的旧对象发生 identity 冲突。
        user.user_roles.clear()
        db.flush()
        for role in roles:
            user.user_roles.append(UserRole(role_id=role.id))
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
        username: Optional[str] = None,
    ) -> dict:
        """更新当前用户的用户名、手机号和邮箱。"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if username is not None:
            normalized_username = username.strip()
            if not 3 <= len(normalized_username) <= 50:
                raise HTTPException(status_code=400, detail="用户名长度必须为 3-50 个字符")
            duplicate_username = (
                db.query(User)
                .filter(
                    User.username == normalized_username,
                    User.id != user_id,
                )
                .first()
            )
            if duplicate_username:
                raise HTTPException(status_code=400, detail="用户名已被其他用户使用")
            user.username = normalized_username
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
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail="用户名或邮箱已被其他用户使用",
            ) from exc
        db.refresh(user)
        logger.info("用户 %s 更新了个人信息", user.username)
        return {
            "message": "个人信息已更新",
            "user": UserService._serialize_user(user, db),
        }

    @staticmethod
    def _prepare_avatar(image_data: bytes, content_type: str | None) -> bytes:
        """校验并标准化头像，避免直接保存未经检查的用户文件。"""
        if not image_data:
            raise HTTPException(status_code=400, detail="请选择头像图片")
        if len(image_data) > AVATAR_MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="头像图片不能超过 5 MB")
        if content_type not in ALLOWED_AVATAR_CONTENT_TYPES:
            raise HTTPException(status_code=400, detail="头像仅支持 JPG、PNG 或 WebP 格式")

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(image_data)) as source:
                    if source.format not in ALLOWED_AVATAR_FORMATS:
                        raise HTTPException(
                            status_code=400,
                            detail="头像仅支持 JPG、PNG 或 WebP 格式",
                        )
                    width, height = source.size
                    if width <= 0 or height <= 0 or width * height > AVATAR_MAX_PIXELS:
                        raise HTTPException(status_code=400, detail="头像图片尺寸过大")
                    source.load()
                    image = ImageOps.exif_transpose(source)
                    if image.mode in {"RGBA", "LA"} or (
                        image.mode == "P" and "transparency" in image.info
                    ):
                        rgba = image.convert("RGBA")
                        background = Image.new("RGBA", rgba.size, "white")
                        background.alpha_composite(rgba)
                        image = background.convert("RGB")
                    else:
                        image = image.convert("RGB")
                    image = ImageOps.fit(
                        image,
                        AVATAR_OUTPUT_SIZE,
                        method=Image.Resampling.LANCZOS,
                    )
                    output = io.BytesIO()
                    image.save(output, format="JPEG", quality=88, optimize=True)
                    return output.getvalue()
        except HTTPException:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            ValueError,
        ) as exc:
            raise HTTPException(status_code=400, detail="无法识别或处理该头像图片") from exc

    @staticmethod
    def update_avatar(
        db: Session,
        user_id: int,
        image_data: bytes,
        content_type: str | None,
    ) -> dict:
        """上传并替换当前用户头像，数据库只保存 MinIO 永久对象名。"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        avatar_data = UserService._prepare_avatar(image_data, content_type)
        try:
            minio = MinIOClient()
            object_name = f"avatars/{user.id}/{uuid.uuid4().hex}.jpg"
            minio.upload_bytes(object_name, avatar_data, content_type="image/jpeg")
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("用户 %s 上传头像失败", user.username)
            raise HTTPException(status_code=503, detail="头像存储服务暂不可用") from exc

        old_avatar = user.avatar
        try:
            user.avatar = object_name
            db.commit()
            db.refresh(user)
        except Exception:
            db.rollback()
            try:
                minio.delete_file(object_name)
            except Exception:
                logger.warning("回滚头像更新时未能清理对象 %s", object_name)
            raise

        old_object_name = None
        if old_avatar:
            old_object_name = (
                minio.object_name_from_url(old_avatar)
                if old_avatar.startswith(("http://", "https://"))
                else old_avatar
            )
        if old_object_name and old_object_name.startswith(f"avatars/{user.id}/"):
            try:
                minio.delete_file(old_object_name)
            except Exception as exc:
                logger.warning("旧头像对象清理失败 %s: %s", old_object_name, str(exc))

        logger.info("用户 %s 更新了头像", user.username)
        return {
            "message": "头像已更新",
            "user": UserService._serialize_user(user, db),
        }

    @staticmethod
    def remove_avatar(db: Session, user_id: int) -> dict:
        """移除当前头像，前端将回退为用户名首字母。"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        old_avatar = user.avatar
        user.avatar = None
        db.commit()
        db.refresh(user)

        if old_avatar:
            try:
                minio = MinIOClient()
                old_object_name = (
                    minio.object_name_from_url(old_avatar)
                    if old_avatar.startswith(("http://", "https://"))
                    else old_avatar
                )
                if old_object_name and old_object_name.startswith(f"avatars/{user.id}/"):
                    minio.delete_file(old_object_name)
            except Exception as exc:
                logger.warning("移除头像后对象清理失败: %s", str(exc))

        logger.info("用户 %s 移除了头像", user.username)
        return {
            "message": "已恢复默认头像",
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
        UserService.revoke_all_auth_sessions(db, user.id)
        db.commit()
        logger.info("用户 %s 修改了密码", user.username)
        return {"message": "密码修改成功"}


# 全局单例
user_service = UserService()
