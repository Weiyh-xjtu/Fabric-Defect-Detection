"""add attachments column to chat_messages

为对话消息新增 attachments JSON 字段，存储检测标注图/视频的 MinIO 永久对象
标识（object_name），用于历史会话还原图片和视频。不存易过期的预签名 URL。

Revision ID: b1c2d3e4f5a6
Revises: accaa411439f
Create Date: 2026-07-08 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'accaa411439f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'chat_messages',
        sa.Column(
            'attachments',
            sa.JSON(),
            nullable=True,
            comment='检测结果附件的 MinIO 对象引用，用于历史还原图片/视频',
        ),
    )


def downgrade() -> None:
    op.drop_column('chat_messages', 'attachments')
