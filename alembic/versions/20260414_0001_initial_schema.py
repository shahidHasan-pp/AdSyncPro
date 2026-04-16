"""Initial AdSync Pro schema.

Revision ID: 20260414_0001
Revises:
Create Date: 2026-04-14
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260414_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=False)

    op.create_table(
        "campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_campaigns_owner_id"), "campaigns", ["owner_id"], unique=False)

    op.create_table(
        "video_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("youtube_url", sa.String(length=1024), nullable=False),
        sa.Column("video_id", sa.String(length=64), nullable=False),
        sa.Column("ad_start_seconds", sa.Integer(), nullable=False),
        sa.Column("ad_end_seconds", sa.Integer(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("is_authorized", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id"),
    )
    op.create_index(op.f("ix_video_metrics_campaign_id"), "video_metrics", ["campaign_id"], unique=False)
    op.create_index(op.f("ix_video_metrics_video_id"), "video_metrics", ["video_id"], unique=False)

    op.create_table(
        "daily_stats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("total_views", sa.Integer(), nullable=False),
        sa.Column("retention_at_ad_start", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["video_id"], ["video_metrics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id", "date", name="uq_daily_stats_video_date"),
    )
    op.create_index(op.f("ix_daily_stats_date"), "daily_stats", ["date"], unique=False)
    op.create_index(op.f("ix_daily_stats_video_id"), "daily_stats", ["video_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_daily_stats_video_id"), table_name="daily_stats")
    op.drop_index(op.f("ix_daily_stats_date"), table_name="daily_stats")
    op.drop_table("daily_stats")

    op.drop_index(op.f("ix_video_metrics_video_id"), table_name="video_metrics")
    op.drop_index(op.f("ix_video_metrics_campaign_id"), table_name="video_metrics")
    op.drop_table("video_metrics")

    op.drop_index(op.f("ix_campaigns_owner_id"), table_name="campaigns")
    op.drop_table("campaigns")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

