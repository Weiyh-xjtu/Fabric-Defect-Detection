"""RBAC permission constants, role definitions, and seed helpers."""

from sqlalchemy.orm import Session

from app.entity.db_models import Permission, Role, RolePermission, User, UserRole


CHAT_USE = "chat:use"
KNOWLEDGE_READ = "knowledge:read"
DETECTION_EXECUTE = "detection:execute"
HISTORY_READ_OWN = "history:read:own"
HISTORY_READ_ANY = "history:read:any"
HISTORY_DELETE_ANY = "history:delete:any"
DASHBOARD_READ_ANY = "dashboard:read:any"
USER_MANAGE = "user:manage"
MODEL_MANAGE = "model:manage"
KNOWLEDGE_MANAGE = "knowledge:manage"
SYSTEM_HEALTH_READ = "system:health:read"

QUALITY_INSPECTOR = "quality_inspector"
PRODUCTION_MANAGER = "production_manager"
SYSTEM_ADMIN = "system_admin"

PERMISSION_DEFINITIONS = {
    CHAT_USE: ("使用智能对话", "agent"),
    KNOWLEDGE_READ: ("检索知识库", "knowledge"),
    DETECTION_EXECUTE: ("执行目标检测", "detection"),
    HISTORY_READ_OWN: ("查看本人检测历史", "history"),
    HISTORY_READ_ANY: ("查看全部检测历史", "history"),
    HISTORY_DELETE_ANY: ("删除检测历史", "history"),
    DASHBOARD_READ_ANY: ("查看全厂数据看板", "dashboard"),
    USER_MANAGE: ("管理用户与角色", "system"),
    MODEL_MANAGE: ("管理训练与模型", "training"),
    KNOWLEDGE_MANAGE: ("管理知识库文件", "knowledge"),
    SYSTEM_HEALTH_READ: ("查看系统运行状态", "system"),
}

ROLE_DEFINITIONS = {
    QUALITY_INSPECTOR: {
        "display_name": "普通质检人员",
        "description": "执行检测并查看本人检测历史",
        "permissions": {CHAT_USE, KNOWLEDGE_READ, DETECTION_EXECUTE, HISTORY_READ_OWN},
    },
    PRODUCTION_MANAGER: {
        "display_name": "生产管理人员",
        "description": "查看全厂检测历史和数据看板",
        "permissions": {CHAT_USE, KNOWLEDGE_READ, HISTORY_READ_ANY, DASHBOARD_READ_ANY},
    },
    SYSTEM_ADMIN: {
        "display_name": "系统管理员",
        "description": "管理平台并拥有全部业务权限",
        "permissions": set(PERMISSION_DEFINITIONS),
    },
}

ALL_PERMISSION_CODES = frozenset(PERMISSION_DEFINITIONS)
LEGACY_ROLE_MAPPING = {
    "admin": SYSTEM_ADMIN,
    "operator": QUALITY_INSPECTOR,
    "viewer": PRODUCTION_MANAGER,
}


def initialize_rbac(db: Session, migrate_existing_users: bool = True) -> None:
    """Idempotently seed roles/permissions and map existing users."""
    permissions = {item.code: item for item in db.query(Permission).all()}
    for code, (name, module) in PERMISSION_DEFINITIONS.items():
        permission = permissions.get(code)
        if permission is None:
            permission = Permission(code=code, name=name, module=module)
            db.add(permission)
            permissions[code] = permission
        else:
            permission.name = name
            permission.module = module
    db.flush()

    roles = {item.name: item for item in db.query(Role).all()}
    for role_name, definition in ROLE_DEFINITIONS.items():
        role = roles.get(role_name)
        if role is None:
            role = Role(name=role_name)
            db.add(role)
            roles[role_name] = role
        role.display_name = definition["display_name"]
        role.description = definition["description"]
        role.is_system = True
    db.flush()

    # Standard roles are authoritative: remove stale grants left by older data.
    for role_name, definition in ROLE_DEFINITIONS.items():
        desired_permission_ids = {
            permissions[code].id for code in definition["permissions"]
        }
        db.query(RolePermission).filter(
            RolePermission.role_id == roles[role_name].id,
            ~RolePermission.permission_id.in_(desired_permission_ids),
        ).delete(synchronize_session=False)

    role_permission_pairs = {
        (item.role_id, item.permission_id) for item in db.query(RolePermission).all()
    }
    for role_name, definition in ROLE_DEFINITIONS.items():
        role = roles[role_name]
        for code in definition["permissions"]:
            pair = (role.id, permissions[code].id)
            if pair not in role_permission_pairs:
                db.add(RolePermission(role_id=pair[0], permission_id=pair[1]))
                role_permission_pairs.add(pair)

    if migrate_existing_users:
        user_role_pairs = {
            (item.user_id, item.role_id) for item in db.query(UserRole).all()
        }
        for user in db.query(User).all():
            current_names = {
                item.role.name for item in user.user_roles if item.role is not None
            }
            targets = {
                target
                for legacy, target in LEGACY_ROLE_MAPPING.items()
                if legacy in current_names
            }
            if user.is_superuser:
                targets.add(SYSTEM_ADMIN)
            if not current_names:
                targets.add(QUALITY_INSPECTOR)
            for role_name in targets:
                pair = (user.id, roles[role_name].id)
                if pair not in user_role_pairs:
                    db.add(UserRole(user_id=pair[0], role_id=pair[1]))
                    user_role_pairs.add(pair)

    db.commit()


def get_user_permission_codes(db: Session, user: User) -> set[str]:
    """Return the union of every permission granted by the user's roles."""
    if user.is_superuser:
        return set(ALL_PERMISSION_CODES)
    rows = (
        db.query(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .filter(UserRole.user_id == user.id)
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def user_has_permission(db: Session, user: User, code: str) -> bool:
    return user.is_superuser or code in get_user_permission_codes(db, user)
