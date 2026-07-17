"""add model backup metadata

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-17 13:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_versions",
        sa.Column("minio_object_name", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "model_versions",
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "model_versions",
        sa.Column("backed_up_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "model_versions",
        sa.Column("archived_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "model_versions",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_versions", "deleted_at")
    op.drop_column("model_versions", "archived_at")
    op.drop_column("model_versions", "backed_up_at")
    op.drop_column("model_versions", "file_sha256")
    op.drop_column("model_versions", "minio_object_name")
