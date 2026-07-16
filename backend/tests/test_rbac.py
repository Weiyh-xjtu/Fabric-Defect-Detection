"""RBAC seed, authentication, and permission matrix tests."""

import json

from sqlalchemy.orm import sessionmaker

from app.config.settings import settings
from app.core.security import create_access_token
from app.core.rbac import (
    ALL_PERMISSION_CODES,
    QUALITY_INSPECTOR,
    ROLE_DEFINITIONS,
    SYSTEM_ADMIN,
    initialize_rbac,
)
from app.entity.db_models import Permission, Role, RolePermission, User, UserRole


def _register_and_login(client, username: str) -> tuple[dict, dict[str, str]]:
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
    return login.json(), {
        "Authorization": f"Bearer {login.json()['access_token']}"
    }


def _grant_role(db, username: str, role_name: str) -> None:
    user = db.query(User).filter_by(username=username).one()
    role = db.query(Role).filter_by(name=role_name).one()
    if not any(item.role_id == role.id for item in user.user_roles):
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()


def test_rbac_seed_is_idempotent_and_complete(db_session):
    quality_role = db_session.query(Role).filter_by(name=QUALITY_INSPECTOR).one()
    stale_permission = db_session.query(Permission).filter_by(
        code="dashboard:read:any"
    ).one()
    if not db_session.query(RolePermission).filter_by(
        role_id=quality_role.id,
        permission_id=stale_permission.id,
    ).first():
        db_session.add(
            RolePermission(
                role_id=quality_role.id,
                permission_id=stale_permission.id,
            )
        )
        db_session.commit()

    initialize_rbac(db_session)
    initialize_rbac(db_session)

    assert {
        item.code for item in db_session.query(Permission).all()
    }.issuperset(ALL_PERMISSION_CODES)
    for role_name, definition in ROLE_DEFINITIONS.items():
        role = db_session.query(Role).filter_by(name=role_name).one()
        granted = {item.permission.code for item in role.role_permissions}
        assert granted == definition["permissions"]

    pairs = db_session.query(RolePermission.role_id, RolePermission.permission_id).all()
    assert len(pairs) == len(set(pairs))


def test_rbac_seed_maps_legacy_and_unassigned_users(db_session):
    legacy_roles = {}
    for name in ("admin", "operator", "viewer"):
        role = db_session.query(Role).filter_by(name=name).first()
        if role is None:
            role = Role(
                name=name,
                display_name=f"legacy-{name}",
                is_system=True,
            )
            db_session.add(role)
        legacy_roles[name] = role
    db_session.flush()

    users = {
        name: User(
            username=f"rbac_legacy_{name}",
            email=f"rbac_legacy_{name}@example.com",
            hashed_password="not-used",
            is_superuser=(name == "superuser"),
        )
        for name in ("admin", "operator", "viewer", "unassigned", "superuser")
    }
    db_session.add_all(users.values())
    db_session.flush()
    for name in ("admin", "operator", "viewer"):
        db_session.add(
            UserRole(user_id=users[name].id, role_id=legacy_roles[name].id)
        )
    db_session.commit()

    initialize_rbac(db_session)
    db_session.expire_all()

    expected = {
        "admin": "system_admin",
        "operator": "quality_inspector",
        "viewer": "production_manager",
        "unassigned": "quality_inspector",
        "superuser": "system_admin",
    }
    for name, mapped_role in expected.items():
        user = db_session.query(User).filter_by(username=f"rbac_legacy_{name}").one()
        assert mapped_role in {item.role.name for item in user.user_roles}


def test_register_assigns_quality_role_and_returns_permissions(client):
    login, headers = _register_and_login(client, "rbac_default_inspector")

    assert QUALITY_INSPECTOR in login["user"]["roles"]
    assert "detection:execute" in login["user"]["permissions"]
    assert "dashboard:read:any" not in login["user"]["permissions"]
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["permissions"] == login["user"]["permissions"]


def test_role_changes_apply_to_existing_access_token(client, db_session):
    _, target_headers = _register_and_login(client, "rbac_dynamic_target")
    _, admin_headers = _register_and_login(client, "rbac_dynamic_admin")
    _grant_role(db_session, "rbac_dynamic_admin", SYSTEM_ADMIN)

    denied = client.get("/api/dashboard/statistics", headers=target_headers)
    assert denied.status_code == 403

    target = db_session.query(User).filter_by(username="rbac_dynamic_target").one()
    promoted = client.put(
        f"/api/user/{target.id}/roles",
        json={"role_names": ["production_manager"]},
        headers=admin_headers,
    )
    assert promoted.status_code == 200
    assert client.get(
        "/api/dashboard/statistics", headers=target_headers
    ).status_code == 200
    assert client.post("/api/detection/single", headers=target_headers).status_code == 403

    demoted = client.put(
        f"/api/user/{target.id}/roles",
        json={"role_names": [QUALITY_INSPECTOR]},
        headers=admin_headers,
    )
    assert demoted.status_code == 200
    assert client.get(
        "/api/dashboard/statistics", headers=target_headers
    ).status_code == 403


def test_disabled_user_loses_access_login_and_refresh(client, db_session):
    _, target_headers = _register_and_login(client, "rbac_disabled_target")
    target_refresh = client.cookies.get(settings.REFRESH_COOKIE_NAME)
    _, admin_headers = _register_and_login(client, "rbac_disabled_admin")
    _grant_role(db_session, "rbac_disabled_admin", SYSTEM_ADMIN)
    target = db_session.query(User).filter_by(username="rbac_disabled_target").one()

    disabled = client.put(
        f"/api/user/{target.id}/status",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert disabled.status_code == 200
    assert client.get("/api/auth/me", headers=target_headers).status_code == 403
    assert client.post(
        "/api/auth/login",
        json={"username": "rbac_disabled_target", "password": "123456"},
    ).status_code == 403

    client.cookies.set(
        settings.REFRESH_COOKIE_NAME,
        target_refresh,
        path="/api/auth",
    )
    assert client.post("/api/auth/refresh").status_code == 401


def test_admin_cannot_disable_self(client, db_session):
    _, admin_headers = _register_and_login(client, "rbac_self_admin")
    _grant_role(db_session, "rbac_self_admin", SYSTEM_ADMIN)
    admin = db_session.query(User).filter_by(username="rbac_self_admin").one()

    response = client.put(
        f"/api/user/{admin.id}/status",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert response.status_code == 400


def test_agent_detection_tool_rechecks_database_permission(db_session, monkeypatch):
    from app.agent import detection_agent as agent_module

    manager = User(
        username="rbac_agent_manager",
        email="rbac_agent_manager@example.com",
        hashed_password="not-used",
    )
    manager_role = db_session.query(Role).filter_by(name="production_manager").one()
    db_session.add(manager)
    db_session.flush()
    db_session.add(UserRole(user_id=manager.id, role_id=manager_role.id))
    db_session.commit()

    test_factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(agent_module, "SessionLocal", test_factory)
    monkeypatch.setattr(
        agent_module.detection_service,
        "detect_single",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("无检测权限时不应调用检测服务")
        ),
    )
    context_token = agent_module._current_user_id.set(manager.id)
    try:
        result = json.loads(
            agent_module.detect_single_image.invoke({"image_path": "fabric.jpg"})
        )
    finally:
        agent_module._current_user_id.reset(context_token)

    assert result["required_permission"] == "detection:execute"


def test_camera_token_checks_active_user_and_detection_permission(
    db_session, monkeypatch
):
    from app.api import detection as detection_api

    inspector = User(
        username="rbac_camera_inspector",
        email="rbac_camera_inspector@example.com",
        hashed_password="not-used",
    )
    manager = User(
        username="rbac_camera_manager",
        email="rbac_camera_manager@example.com",
        hashed_password="not-used",
    )
    inspector_role = db_session.query(Role).filter_by(name=QUALITY_INSPECTOR).one()
    manager_role = db_session.query(Role).filter_by(name="production_manager").one()
    db_session.add_all([inspector, manager])
    db_session.flush()
    db_session.add_all(
        [
            UserRole(user_id=inspector.id, role_id=inspector_role.id),
            UserRole(user_id=manager.id, role_id=manager_role.id),
        ]
    )
    db_session.commit()

    test_factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(detection_api, "SessionLocal", test_factory)
    inspector_token = create_access_token({"sub": str(inspector.id)})
    manager_token = create_access_token({"sub": str(manager.id)})

    authenticated, code = detection_api._authenticate_camera_token(inspector_token)
    assert authenticated.id == inspector.id
    assert code == 0
    assert detection_api._authenticate_camera_token(manager_token) == (None, 4403)
    assert detection_api._authenticate_camera_token(None) == (None, 4401)

    inspector.is_active = False
    db_session.commit()
    assert detection_api._authenticate_camera_token(inspector_token) == (None, 4401)
