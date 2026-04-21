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


# ---------------------------------------------------------------------------
# Metric sub-schemas (aligned with gcp_script.py analytics categories)
# ---------------------------------------------------------------------------

class VideoMetadata(BaseModel):
    title: str | None = None
    description: str | None = None
    channel_id: str | None = None
    channel_title: str | None = None
    duration_seconds: int | None = None
    published_at: str | None = None
    privacy_status: str | None = None


class ViewsReachMetrics(BaseModel):
    total_views: int | None = None
    uniques: int | None = None
    engaged_views: int | None = None
    thumbnail_impressions: int | None = None
    thumbnail_ctr: float | None = None


class WatchTimeMetrics(BaseModel):
    estimated_minutes_watched: float | None = None
    average_view_duration: float | None = None
    average_view_percentage: float | None = None


class MonetizationMetrics(BaseModel):
    estimated_revenue: float | None = None
    estimated_ad_revenue: float | None = None
    ad_impressions: int | None = None
    monetized_playbacks: int | None = None
    playback_based_cpm: float | None = None
    cpm: float | None = None


class EngagementMetrics(BaseModel):
    likes: int | None = None
    dislikes: int | None = None
    comments: int | None = None
    shares: int | None = None
    subscribers_gained: int | None = None
    subscribers_lost: int | None = None


class InteractiveMetrics(BaseModel):
    cards_impressions: int | None = None
    cards_click_rate: float | None = None
    end_screen_click_rate: float | None = None


# ---------------------------------------------------------------------------
# Public/Private metrics (kept for backward-compat in dashboard cards)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Dashboard video item (campaign dashboard card view)
# ---------------------------------------------------------------------------

class DashboardVideoItem(BaseModel):
    video_record_id: uuid.UUID
    video_id: str
    youtube_url: str
    title: str | None = None
    channel_title: str | None = None
    duration_seconds: int | None = None
    ad_start_seconds: int
    ad_end_seconds: int
    is_authorized: bool
    public_metrics: PublicMetrics
    private_metrics: PrivateMetrics


# ---------------------------------------------------------------------------
# Full video detail (all metric categories)
# ---------------------------------------------------------------------------

class VideoDetailResponse(BaseModel):
    video_record_id: uuid.UUID
    video_id: str
    youtube_url: str
    campaign_id: uuid.UUID
    ad_start_seconds: int
    ad_end_seconds: int
    is_authorized: bool
    last_updated: datetime | None = None

    metadata: VideoMetadata = Field(default_factory=VideoMetadata)
    views_reach: ViewsReachMetrics = Field(default_factory=ViewsReachMetrics)
    watch_time: WatchTimeMetrics = Field(default_factory=WatchTimeMetrics)
    monetization: MonetizationMetrics = Field(default_factory=MonetizationMetrics)
    engagement: EngagementMetrics = Field(default_factory=EngagementMetrics)
    interactive: InteractiveMetrics = Field(default_factory=InteractiveMetrics)
    retention: PrivateMetrics = Field(default_factory=PrivateMetrics)


# ---------------------------------------------------------------------------
# Video comparison item (flat row for comparison table)
# ---------------------------------------------------------------------------

class VideoComparisonItem(BaseModel):
    video_id: str
    title: str | None = None
    channel_title: str | None = None
    duration_seconds: int | None = None
    total_views: int | None = None
    uniques: int | None = None
    likes: int | None = None
    dislikes: int | None = None
    comments: int | None = None
    shares: int | None = None
    subscriber_count: int | None = None
    estimated_minutes_watched: float | None = None
    average_view_duration: float | None = None
    average_view_percentage: float | None = None
    estimated_revenue: float | None = None
    ad_impressions: int | None = None
    cpm: float | None = None
    retention_at_ad_start: float | None = None
    subscribers_gained: int | None = None
    subscribers_lost: int | None = None
