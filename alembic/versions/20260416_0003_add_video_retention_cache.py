"""Add retention cache fields to video_metrics

Revision ID: 20260416_0003
Revises: 20260414_0002
Create Date: 2026-04-16
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260416_0003"
down_revision: Union[str, None] = "20260414_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("video_metrics", sa.Column("retention_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("video_metrics", sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("video_metrics", "last_updated")
    op.drop_column("video_metrics", "retention_json")

