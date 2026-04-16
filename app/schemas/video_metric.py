import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class RetentionPoint(BaseModel):
    ratio: float
    value: float


class VideoMetricCreate(BaseModel):
    youtube_url: HttpUrl
    ad_start_seconds: int = Field(ge=0)
    ad_end_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "VideoMetricCreate":
        if self.ad_end_seconds <= self.ad_start_seconds:
            raise ValueError("ad_end_seconds must be greater than ad_start_seconds.")
        return self


class VideoMetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    youtube_url: str
    video_id: str
    ad_start_seconds: int
    ad_end_seconds: int
    is_authorized: bool
    oauth_login_url: str | None = None


class PublicMetrics(BaseModel):
    total_views: int | None = None
    likes: int | None = None
    comments: int | None = None
    favorites: int | None = None
    upload_date: datetime | None = None
    subscriber_count: int | None = None


class PrivateMetrics(BaseModel):
    retention_at_start: float | None = None
    retention_at_ad_start: float | None = None
    retention_data: list[RetentionPoint] = Field(default_factory=list)


class DashboardVideoItem(BaseModel):
    video_record_id: uuid.UUID
    video_id: str
    youtube_url: str
    duration_seconds: int | None = None
    ad_start_seconds: int
    ad_end_seconds: int
    is_authorized: bool
    public_metrics: PublicMetrics
    private_metrics: PrivateMetrics
