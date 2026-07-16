"""seed system RBAC roles and permissions

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy.orm import Session

from app.core.rbac import initialize_rbac


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    session = Session(bind=op.get_bind())
    try:
        initialize_rbac(session)
    finally:
        session.close()


def downgrade() -> None:
    # Keep assignments to avoid orphaning users during a rollback.
    pass
