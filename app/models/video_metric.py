import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class VideoMetric(Base):
    __tablename__ = "video_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    youtube_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    video_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    ad_start_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    ad_end_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    retention_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_authorized: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="videos")
    daily_stats: Mapped[list["DailyStat"]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
    )
