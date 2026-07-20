"""add revocable authentication sessions

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-20 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False, comment="JWT sid，会话唯一标识"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="所属用户"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间"),
        sa.Column("expires_at", sa.DateTime(), nullable=False, comment="Refresh 会话过期时间"),
        sa.Column("last_refreshed_at", sa.DateTime(), nullable=False, comment="最后续期时间"),
        sa.Column("revoked_at", sa.DateTime(), nullable=True, comment="撤销时间"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"])
    op.create_index(op.f("ix_auth_sessions_expires_at"), "auth_sessions", ["expires_at"])
    op.create_index(op.f("ix_auth_sessions_revoked_at"), "auth_sessions", ["revoked_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_sessions_revoked_at"), table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_expires_at"), table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
