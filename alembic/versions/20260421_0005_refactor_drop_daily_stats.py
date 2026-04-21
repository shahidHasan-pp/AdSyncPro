"""Refactor: drop daily_stats, add video_quantity to campaigns, expand video_metrics

Revision ID: 20260421_0005
Revises: 20260416_0004
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260421_0005"
down_revision: Union[str, None] = "20260416_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the daily_stats table entirely
    op.drop_table("daily_stats")

    # Add video_quantity to campaigns
    op.add_column(
        "campaigns",
        sa.Column("video_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    # Add new columns to video_metrics
    op.add_column(
        "video_metrics",
        sa.Column("title", sa.String(512), nullable=True),
    )
    op.add_column(
        "video_metrics",
        sa.Column("channel_title", sa.String(255), nullable=True),
    )
    op.add_column(
        "video_metrics",
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "video_metrics",
        sa.Column("total_views", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "video_metrics",
        sa.Column("likes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "video_metrics",
        sa.Column("upload_date", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Remove added columns from video_metrics
    op.drop_column("video_metrics", "upload_date")
    op.drop_column("video_metrics", "likes")
    op.drop_column("video_metrics", "total_views")
    op.drop_column("video_metrics", "duration_seconds")
    op.drop_column("video_metrics", "channel_title")
    op.drop_column("video_metrics", "title")

    # Remove video_quantity from campaigns
    op.drop_column("campaigns", "video_quantity")

    # Re-create daily_stats table
    op.create_table(
        "daily_stats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("video_id", sa.UUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("total_views", sa.Integer(), nullable=False),
        sa.Column("retention_at_ad_start", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["video_id"], ["video_metrics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id", "date", name="uq_daily_stats_video_date"),
    )
    op.create_index("ix_daily_stats_video_id", "daily_stats", ["video_id"])
    op.create_index("ix_daily_stats_date", "daily_stats", ["date"])
