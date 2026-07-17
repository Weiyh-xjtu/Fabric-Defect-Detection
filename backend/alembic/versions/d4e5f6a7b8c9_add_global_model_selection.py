"""add global model selection

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-17 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_versions",
        sa.Column(
            "is_global_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="是否为全局检测启用模型（全系统最多一个）",
        ),
    )
    # 兼容已有数据：优先沿用最新的场景默认模型，否则选择最新的启用版本。
    op.execute(
        """
        UPDATE model_versions
        SET is_global_default = TRUE
        WHERE id = (
            SELECT id
            FROM model_versions
            WHERE status = 'active'
            ORDER BY is_default DESC, created_at DESC, id DESC
            LIMIT 1
        )
        """
    )
    op.create_index(
        "uq_model_versions_global_default",
        "model_versions",
        ["is_global_default"],
        unique=True,
        postgresql_where=sa.text("is_global_default"),
    )
    op.alter_column("model_versions", "is_global_default", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "uq_model_versions_global_default",
        table_name="model_versions",
    )
    op.drop_column("model_versions", "is_global_default")
