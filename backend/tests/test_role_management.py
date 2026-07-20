"""角色权限编辑与角色增删改 API 测试。"""

from app.core.rbac import (
    CHAT_USE,
    DETECTION_EXECUTE,
    QUALITY_INSPECTOR,
    ROLE_DEFINITIONS,
    SYSTEM_ADMIN,
    USER_MANAGE,
)
from app.entity.db_models import Permission, Role, RolePermission, User, UserRole


def _register_and_login(client, username: str) -> dict[str, str]:
    password = "123456"
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": password,
        },
    )
    assert response.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _grant_role(db, username: str, role_name: str) -> None:
    user = db.query(User).filter_by(username=username).one()
    role = db.query(Role).filter_by(name=role_name).one()
    if not any(item.role_id == role.id for item in user.user_roles):
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()


def _admin_headers(client, db, username: str) -> dict[str, str]:
    headers = _register_and_login(client, username)
    _grant_role(db, username, SYSTEM_ADMIN)
    return headers


def _role_id(db, name: str) -> int:
    return db.query(Role).filter_by(name=name).one().id


def _restore_default_permissions(db, role_name: str) -> None:
    """将系统角色的权限恢复为 ROLE_DEFINITIONS 中的默认集合。"""
    role = db.query(Role).filter_by(name=role_name).one()
    db.query(RolePermission).filter_by(role_id=role.id).delete(
        synchronize_session=False
    )
    for code in ROLE_DEFINITIONS[role_name]["permissions"]:
        permission = db.query(Permission).filter_by(code=code).one()
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    db.commit()


def test_list_permissions_requires_user_manage(client, db_session):
    headers = _register_and_login(client, "perm_reader_plain")
    assert client.get("/api/user/permissions", headers=headers).status_code == 403

    admin_headers = _admin_headers(client, db_session, "perm_reader_admin")
    response = client.get("/api/user/permissions", headers=admin_headers)
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["permissions"]}
    assert {USER_MANAGE, DETECTION_EXECUTE, CHAT_USE}.issubset(codes)


def test_update_role_permissions(client, db_session):
    headers = _admin_headers(client, db_session, "role_perm_admin")
    role_id = _role_id(db_session, QUALITY_INSPECTOR)

    response = client.put(
        f"/api/user/roles/{role_id}/permissions",
        json={"permission_codes": [CHAT_USE, DETECTION_EXECUTE]},
        headers=headers,
    )
    assert response.status_code == 200
    assert set(response.json()["permissions"]) == {CHAT_USE, DETECTION_EXECUTE}

    # 未知权限被拒绝
    bad = client.put(
        f"/api/user/roles/{role_id}/permissions",
        json={"permission_codes": ["not:a:permission"]},
        headers=headers,
    )
    assert bad.status_code == 400

    # 恢复默认权限，避免影响其他测试（seed 不再覆盖已有角色的授权）
    _restore_default_permissions(db_session, QUALITY_INSPECTOR)


def test_cannot_strip_last_user_manage(client, db_session):
    headers = _admin_headers(client, db_session, "last_admin_guard")
    role_id = _role_id(db_session, SYSTEM_ADMIN)

    # 其他测试可能遗留活跃的超级管理员（其不受角色权限限制），
    # 临时停用它们以验证“最后一个用户管理入口”保护。
    bypass_users = (
        db_session.query(User)
        .filter(User.is_superuser.is_(True), User.is_active.is_(True))
        .all()
    )
    for user in bypass_users:
        user.is_active = False
    db_session.commit()

    try:
        response = client.put(
            f"/api/user/roles/{role_id}/permissions",
            json={"permission_codes": [CHAT_USE]},
            headers=headers,
        )
        assert response.status_code == 400
        assert "用户管理" in response.json()["message"]
    finally:
        for user in bypass_users:
            user.is_active = True
        db_session.commit()


def test_create_update_delete_custom_role(client, db_session):
    headers = _admin_headers(client, db_session, "role_crud_admin")

    created = client.post(
        "/api/user/roles",
        json={
            "name": "workshop_leader",
            "display_name": "车间班组长",
            "description": "负责车间调度",
            "permission_codes": [CHAT_USE],
        },
        headers=headers,
    )
    assert created.status_code == 201
    role = created.json()
    assert role["name"] == "workshop_leader"
    assert role["is_system"] is False
    assert role["permissions"] == [CHAT_USE]

    # 重复标识被拒绝
    dup = client.post(
        "/api/user/roles",
        json={"name": "workshop_leader", "display_name": "x", "permission_codes": []},
        headers=headers,
    )
    assert dup.status_code == 400

    # 重命名
    renamed = client.put(
        f"/api/user/roles/{role['id']}",
        json={"name": "workshop_chief", "display_name": "车间主管"},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "workshop_chief"
    assert renamed.json()["display_name"] == "车间主管"

    # 分配给用户后删除，用户关联被解除
    _register_and_login(client, "role_crud_member")
    _grant_role(db_session, "role_crud_member", "workshop_chief")

    deleted = client.delete(f"/api/user/roles/{role['id']}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "affected_users": 1}
    assert db_session.query(Role).filter_by(name="workshop_chief").first() is None


def test_system_role_rename_and_delete_forbidden(client, db_session):
    headers = _admin_headers(client, db_session, "sys_role_admin")
    role_id = _role_id(db_session, QUALITY_INSPECTOR)

    renamed = client.put(
        f"/api/user/roles/{role_id}",
        json={"name": "renamed_inspector"},
        headers=headers,
    )
    assert renamed.status_code == 400

    # 但显示名/描述可以修改
    display = client.put(
        f"/api/user/roles/{role_id}",
        json={"display_name": "质检专员", "description": "新描述"},
        headers=headers,
    )
    assert display.status_code == 200
    assert display.json()["display_name"] == "质检专员"

    deleted = client.delete(f"/api/user/roles/{role_id}", headers=headers)
    assert deleted.status_code == 400

    # 恢复默认显示名
    role = db_session.query(Role).filter_by(name=QUALITY_INSPECTOR).one()
    role.display_name = "普通质检人员"
    role.description = "执行检测并查看本人检测历史"
    db_session.commit()
