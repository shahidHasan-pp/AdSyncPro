import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DailyStat(Base):
    __tablename__ = "daily_stats"
    __table_args__ = (UniqueConstraint("video_id", "date", name="uq_daily_stats_video_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_metrics.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total_views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retention_at_ad_start: Mapped[float | None] = mapped_column(Float, nullable=True)

    video: Mapped["VideoMetric"] = relationship(back_populates="daily_stats")
