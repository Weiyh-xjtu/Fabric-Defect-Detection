"""首次启动管理员创建与恢复命令底层逻辑测试。"""
import io

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.rbac import SYSTEM_ADMIN, initialize_rbac
from app.core.security import hash_password, verify_password
from app.database.session import Base
from app.entity.db_models import Role, User, UserRole
from app.services.admin_bootstrap_service import (
    ensure_bootstrap_admin,
    has_admin,
    print_admin_credentials,
    recover_admin,
)


class InteractiveBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def isolated_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    initialize_rbac(session)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_bootstrap_creates_exactly_one_admin(isolated_db):
    credentials = ensure_bootstrap_admin(isolated_db)

    assert credentials is not None
    assert credentials.username == "admin"
    assert has_admin(isolated_db) is True
    user = isolated_db.query(User).filter(User.username == "admin").one()
    assert user.is_active is True
    assert verify_password(credentials.password, user.hashed_password)
    assert any(item.role.name == SYSTEM_ADMIN for item in user.user_roles)

    assert ensure_bootstrap_admin(isolated_db) is None
    assert isolated_db.query(User).count() == 1


def test_bootstrap_avoids_existing_normal_username(isolated_db):
    normal_role = isolated_db.query(Role).filter(Role.name == "quality_inspector").one()
    normal_user = User(
        username="admin",
        email="normal@example.com",
        hashed_password=hash_password("123456"),
        is_active=True,
    )
    isolated_db.add(normal_user)
    isolated_db.flush()
    isolated_db.add(UserRole(user_id=normal_user.id, role_id=normal_role.id))
    isolated_db.commit()

    credentials = ensure_bootstrap_admin(isolated_db)

    assert credentials is not None
    assert credentials.username == "admin_2"
    assert isolated_db.query(User).count() == 2


def test_recover_admin_resets_password_and_reactivates_account(isolated_db):
    initial = ensure_bootstrap_admin(isolated_db)
    assert initial is not None
    user = isolated_db.query(User).filter(User.username == initial.username).one()
    user.is_active = False
    isolated_db.commit()
    assert ensure_bootstrap_admin(isolated_db) is None

    recovered = recover_admin(isolated_db)

    isolated_db.refresh(user)
    assert recovered.created is False
    assert recovered.username == initial.username
    assert user.is_active is True
    assert verify_password(recovered.password, user.hashed_password)
    assert not verify_password(initial.password, user.hashed_password)


def test_recover_named_user_promotes_existing_account(isolated_db):
    user = User(
        username="existing_operator",
        email="existing_operator@example.com",
        hashed_password=hash_password("123456"),
        is_active=False,
    )
    isolated_db.add(user)
    isolated_db.commit()

    recovered = recover_admin(isolated_db, "existing_operator")

    isolated_db.refresh(user)
    assert recovered.created is False
    assert user.is_active is True
    assert any(item.role.name == SYSTEM_ADMIN for item in user.user_roles)
    assert verify_password(recovered.password, user.hashed_password)


def test_credentials_only_print_to_interactive_terminal(isolated_db):
    credentials = ensure_bootstrap_admin(isolated_db)
    assert credentials is not None
    terminal = InteractiveBuffer()
    redirected_output = io.StringIO()

    assert print_admin_credentials(credentials, terminal) is True
    assert credentials.username in terminal.getvalue()
    assert credentials.password in terminal.getvalue()

    assert print_admin_credentials(credentials, redirected_output) is False
    assert redirected_output.getvalue() == ""
