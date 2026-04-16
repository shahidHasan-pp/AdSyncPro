"""Add public stats columns to video_metrics

Revision ID: 20260416_0004
Revises: 20260416_0003
Create Date: 2026-04-16
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260416_0004"
down_revision: Union[str, None] = "20260416_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("video_metrics", sa.Column("comment_count", sa.Integer(), nullable=True))
    op.add_column("video_metrics", sa.Column("subscriber_count", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_metrics", "subscriber_count")
    op.drop_column("video_metrics", "comment_count")

