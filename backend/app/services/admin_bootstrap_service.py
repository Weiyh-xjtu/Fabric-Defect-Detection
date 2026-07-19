"""首次启动管理员创建与交互式恢复服务。"""
from __future__ import annotations

import secrets
import sys
from dataclasses import dataclass
from typing import TextIO

from sqlalchemy import or_, text
from sqlalchemy.orm import Query, Session

from app.core.rbac import SYSTEM_ADMIN
from app.core.security import hash_password
from app.entity.db_models import Role, User, UserRole


BOOTSTRAP_LOCK_KEY = 0x464142524943
BOOTSTRAP_USERNAME_LETTERS = "abcdefghjkmnpqrstuvwxyz"
BOOTSTRAP_USERNAME_ALPHABET = f"{BOOTSTRAP_USERNAME_LETTERS}23456789"
BOOTSTRAP_USERNAME_LENGTH = 8
BOOTSTRAP_PASSWORD_ALPHABET = (
    "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
)
BOOTSTRAP_PASSWORD_LENGTH = 10


@dataclass(frozen=True)
class AdminCredentials:
    """只在当前进程内短暂传递的管理员明文凭据。"""

    username: str
    password: str
    created: bool


def _lock_bootstrap_transaction(db: Session) -> None:
    """PostgreSQL 多进程启动时串行执行管理员检查。"""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": BOOTSTRAP_LOCK_KEY},
        )


def _admin_query(db: Session) -> Query:
    """管理员以超级管理员标记或 system_admin 角色为准。"""
    return (
        db.query(User)
        .outerjoin(UserRole, UserRole.user_id == User.id)
        .outerjoin(Role, Role.id == UserRole.role_id)
        .filter(or_(User.is_superuser.is_(True), Role.name == SYSTEM_ADMIN))
        .distinct()
    )


def has_admin(db: Session) -> bool:
    """判断系统中是否已经存在管理员账号，包括暂时禁用的管理员。"""
    return _admin_query(db).first() is not None


def _system_admin_role(db: Session) -> Role:
    role = db.query(Role).filter(Role.name == SYSTEM_ADMIN).first()
    if role is None:
        raise RuntimeError("系统管理员角色尚未初始化")
    return role


def _generate_password() -> str:
    """生成固定 10 位、便于终端复制的一次性随机密码。"""
    return "".join(
        secrets.choice(BOOTSTRAP_PASSWORD_ALPHABET)
        for _ in range(BOOTSTRAP_PASSWORD_LENGTH)
    )


def _generate_username() -> str:
    """生成以字母开头、不含易混淆字符的 8 位随机用户名。"""
    first = secrets.choice(BOOTSTRAP_USERNAME_LETTERS)
    remainder = "".join(
        secrets.choice(BOOTSTRAP_USERNAME_ALPHABET)
        for _ in range(BOOTSTRAP_USERNAME_LENGTH - 1)
    )
    return f"{first}{remainder}"


def _available_identity(db: Session) -> tuple[str, str]:
    """为自动创建账号随机选择不与现有用户名或邮箱冲突的身份。"""
    while True:
        username = _generate_username()
        email = f"{username}@localhost.invalid"
        occupied = (
            db.query(User.id)
            .filter(or_(User.username == username, User.email == email))
            .first()
        )
        if occupied is None:
            return username, email


def _grant_admin_role(db: Session, user: User, role: Role) -> None:
    exists = (
        db.query(UserRole.id)
        .filter(UserRole.user_id == user.id, UserRole.role_id == role.id)
        .first()
    )
    if exists is None:
        db.add(UserRole(user_id=user.id, role_id=role.id))


def ensure_bootstrap_admin(db: Session) -> AdminCredentials | None:
    """无管理员时自动创建一个 system_admin 账号；重复调用不会重复创建。"""
    _lock_bootstrap_transaction(db)
    if has_admin(db):
        db.commit()
        return None

    role = _system_admin_role(db)
    username, email = _available_identity(db)
    password = _generate_password()
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        is_active=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    _grant_admin_role(db, user, role)
    db.commit()
    return AdminCredentials(username=username, password=password, created=True)


def recover_admin(db: Session, username: str | None = None) -> AdminCredentials:
    """重置指定/唯一管理员密码，或在管理员缺失时创建恢复账号。"""
    _lock_bootstrap_transaction(db)
    role = _system_admin_role(db)
    normalized_username = username.strip() if username else None
    if normalized_username is not None and not 3 <= len(normalized_username) <= 50:
        raise ValueError("用户名长度必须为 3-50 个字符")

    target = None
    if normalized_username:
        target = db.query(User).filter(User.username == normalized_username).first()
    else:
        admins = _admin_query(db).all()
        if len(admins) > 1:
            raise ValueError("系统中存在多个管理员，请使用 --username 指定恢复账号")
        target = admins[0] if admins else None

    created = target is None
    password = _generate_password()
    if target is None:
        if normalized_username:
            email_base = f"{normalized_username}@localhost.invalid"
            email = email_base
            suffix = 2
            while db.query(User.id).filter(User.email == email).first() is not None:
                email = f"{normalized_username}_{suffix}@localhost.invalid"
                suffix += 1
            target = User(
                username=normalized_username,
                email=email,
                hashed_password=hash_password(password),
                is_active=True,
                is_superuser=False,
            )
            db.add(target)
            db.flush()
        else:
            generated_username, email = _available_identity(db)
            target = User(
                username=generated_username,
                email=email,
                hashed_password=hash_password(password),
                is_active=True,
                is_superuser=False,
            )
            db.add(target)
            db.flush()
    else:
        target.hashed_password = hash_password(password)
        target.is_active = True

    _grant_admin_role(db, target, role)
    db.commit()
    return AdminCredentials(
        username=target.username,
        password=password,
        created=created,
    )


def interactive_terminal_stream() -> TextIO | None:
    """返回真实交互式终端，重定向输出或日志捕获环境返回 None。"""
    for stream in (sys.stderr, sys.stdout):
        try:
            if stream is not None and stream.isatty():
                return stream
        except Exception:
            continue
    return None


def print_admin_credentials(
    credentials: AdminCredentials,
    stream: TextIO | None = None,
) -> bool:
    """仅向交互式终端显示凭据，不使用应用 logger。"""
    target = stream or interactive_terminal_stream()
    if target is None or not target.isatty():
        return False
    action = "已创建" if credentials.created else "已恢复"
    target.write(
        "\n"
        "============================================================\n"
        f"系统管理员账号{action}\n"
        f"用户名：{credentials.username}\n"
        f"临时密码：{credentials.password}\n"
        "请立即登录并修改密码；该临时密码不会再次显示。\n"
        "============================================================\n"
    )
    target.flush()
    return True
