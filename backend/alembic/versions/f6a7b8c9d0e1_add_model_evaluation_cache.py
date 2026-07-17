"""add model evaluation cache

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-17 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_evaluations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("training_task_id", sa.Integer(), nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=True),
        sa.Column("weight_sha256", sa.String(length=64), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("split", sa.String(length=20), nullable=False),
        sa.Column("conf", sa.Float(), nullable=False),
        sa.Column("iou", sa.Float(), nullable=False),
        sa.Column("imgsz", sa.Integer(), nullable=False),
        sa.Column("overall", sa.JSON(), nullable=False),
        sa.Column("per_class", sa.JSON(), nullable=False),
        sa.Column("artifact_paths", sa.JSON(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
        sa.ForeignKeyConstraint(["training_task_id"], ["training_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_model_evaluations_training_task_id"),
        "model_evaluations",
        ["training_task_id"],
    )
    op.create_index(
        op.f("ix_model_evaluations_model_version_id"),
        "model_evaluations",
        ["model_version_id"],
    )
    op.create_index(
        op.f("ix_model_evaluations_weight_sha256"),
        "model_evaluations",
        ["weight_sha256"],
    )
    op.create_index(
        op.f("ix_model_evaluations_dataset_fingerprint"),
        "model_evaluations",
        ["dataset_fingerprint"],
    )
    op.create_index(
        op.f("ix_model_evaluations_evaluated_at"),
        "model_evaluations",
        ["evaluated_at"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_model_evaluations_evaluated_at"), table_name="model_evaluations")
    op.drop_index(op.f("ix_model_evaluations_dataset_fingerprint"), table_name="model_evaluations")
    op.drop_index(op.f("ix_model_evaluations_weight_sha256"), table_name="model_evaluations")
    op.drop_index(op.f("ix_model_evaluations_model_version_id"), table_name="model_evaluations")
    op.drop_index(op.f("ix_model_evaluations_training_task_id"), table_name="model_evaluations")
    op.drop_table("model_evaluations")
