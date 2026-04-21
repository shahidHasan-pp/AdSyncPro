"""
Campaign routes — CRUD, dashboard, video detail, comparison.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Body
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_current_user
from app.core.logger import get_logger
from app.models.campaign import Campaign
from app.models.user import User
from app.models.video_metric import VideoMetric
from app.schemas.campaign import CampaignCreate, CampaignDashboardResponse, CampaignRead
from app.schemas.video_metric import (
    DashboardVideoItem,
    EngagementMetrics,
    InteractiveMetrics,
    MonetizationMetrics,
    PrivateMetrics,
    PublicMetrics,
    VideoComparisonItem,
    VideoDetailResponse,
    VideoMetadata,
    VideoMetricCreate,
    VideoMetricRead,
    ViewsReachMetrics,
    WatchTimeMetrics,
)
from app.services.youtube import extract_video_id_from_url
from app.services.youtube_analytics import fetch_retention_data

router = APIRouter(prefix="/campaigns", tags=["Campaign Management"])
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers: build response objects from cache blob
# ---------------------------------------------------------------------------

def _build_dashboard_item(video: VideoMetric, metrics: dict) -> DashboardVideoItem:
    """Build a DashboardVideoItem from a VideoMetric + analytics blob."""
    ps = metrics.get("public_stats", {})
    engagement = metrics.get("engagement", {})
    retention_points = list(metrics.get("retention_points") or [])

    return DashboardVideoItem(
        video_record_id=video.id,
        video_id=video.video_id,
        youtube_url=video.youtube_url,
        title=video.title or (metrics.get("metadata", {}) or {}).get("title"),
        channel_title=video.channel_title or (metrics.get("metadata", {}) or {}).get("channel_title"),
        duration_seconds=ps.get("duration_seconds"),
        ad_start_seconds=video.ad_start_seconds,
        ad_end_seconds=video.ad_end_seconds,
        is_authorized=video.is_authorized,
        public_metrics=PublicMetrics(
            total_views=_int_or_none(ps.get("view_count")),
            likes=_int_or_none(ps.get("like_count")),
            comments=_int_or_none(ps.get("comment_count")),
            favorites=_int_or_none(ps.get("favorite_count")),
            upload_date=_parse_dt(ps.get("upload_date")),
            subscriber_count=_int_or_none(ps.get("subscriber_count")),
        ),
        private_metrics=PrivateMetrics(
            retention_at_start=_float_or_none(metrics.get("retention_at_ad_start")),
            retention_at_ad_start=_float_or_none(metrics.get("retention_at_ad_start")),
            retention_data=retention_points,
        ),
    )


def _build_detail_response(video: VideoMetric, metrics: dict) -> VideoDetailResponse:
    """Build a full VideoDetailResponse from a VideoMetric + analytics blob."""
    meta = metrics.get("metadata", {}) or {}
    vr = metrics.get("views_reach", {}) or {}
    wt = metrics.get("watch_time", {}) or {}
    mon = metrics.get("monetization", {}) or {}
    eng = metrics.get("engagement", {}) or {}
    inter = metrics.get("interactive", {}) or {}
    retention_points = list(metrics.get("retention_points") or [])

    return VideoDetailResponse(
        video_record_id=video.id,
        video_id=video.video_id,
        youtube_url=video.youtube_url,
        campaign_id=video.campaign_id,
        ad_start_seconds=video.ad_start_seconds,
        ad_end_seconds=video.ad_end_seconds,
        is_authorized=video.is_authorized,
        last_updated=video.last_updated,
        metadata=VideoMetadata(
            title=meta.get("title"),
            description=meta.get("description"),
            channel_id=meta.get("channel_id"),
            channel_title=meta.get("channel_title"),
            duration_seconds=meta.get("duration_seconds"),
            published_at=meta.get("published_at"),
            privacy_status=meta.get("privacy_status"),
        ),
        views_reach=ViewsReachMetrics(
            total_views=_int_or_none(vr.get("views")),
            uniques=_int_or_none(vr.get("uniques")),
            engaged_views=_int_or_none(vr.get("engaged_views")),
            thumbnail_impressions=_int_or_none(vr.get("thumbnail_impressions")),
            thumbnail_ctr=_float_or_none(vr.get("thumbnail_ctr")),
        ),
        watch_time=WatchTimeMetrics(
            estimated_minutes_watched=_float_or_none(wt.get("estimated_minutes_watched")),
            average_view_duration=_float_or_none(wt.get("average_view_duration")),
            average_view_percentage=_float_or_none(wt.get("average_view_percentage")),
        ),
        monetization=MonetizationMetrics(
            estimated_revenue=_float_or_none(mon.get("estimated_revenue")),
            estimated_ad_revenue=_float_or_none(mon.get("estimated_ad_revenue")),
            ad_impressions=_int_or_none(mon.get("ad_impressions")),
            monetized_playbacks=_int_or_none(mon.get("monetized_playbacks")),
            playback_based_cpm=_float_or_none(mon.get("playback_based_cpm")),
            cpm=_float_or_none(mon.get("cpm")),
        ),
        engagement=EngagementMetrics(
            likes=_int_or_none(eng.get("likes")),
            dislikes=_int_or_none(eng.get("dislikes")),
            comments=_int_or_none(eng.get("comments")),
            shares=_int_or_none(eng.get("shares")),
            subscribers_gained=_int_or_none(eng.get("subscribers_gained")),
            subscribers_lost=_int_or_none(eng.get("subscribers_lost")),
        ),
        interactive=InteractiveMetrics(
            cards_impressions=_int_or_none(inter.get("cards_impressions")),
            cards_click_rate=_float_or_none(inter.get("cards_click_rate")),
            end_screen_click_rate=_float_or_none(inter.get("end_screen_click_rate")),
        ),
        retention=PrivateMetrics(
            retention_at_start=_float_or_none(metrics.get("retention_at_ad_start")),
            retention_at_ad_start=_float_or_none(metrics.get("retention_at_ad_start")),
            retention_data=retention_points,
        ),
    )


def _build_comparison_item(video: VideoMetric, metrics: dict) -> VideoComparisonItem:
    """Build a flat comparison row."""
    ps = metrics.get("public_stats", {}) or {}
    vr = metrics.get("views_reach", {}) or {}
    wt = metrics.get("watch_time", {}) or {}
    mon = metrics.get("monetization", {}) or {}
    eng = metrics.get("engagement", {}) or {}
    meta = metrics.get("metadata", {}) or {}

    return VideoComparisonItem(
        video_id=video.video_id,
        title=video.title or meta.get("title"),
        channel_title=video.channel_title or meta.get("channel_title"),
        duration_seconds=ps.get("duration_seconds"),
        total_views=_int_or_none(vr.get("views") or ps.get("view_count")),
        uniques=_int_or_none(vr.get("uniques")),
        likes=_int_or_none(eng.get("likes") or ps.get("like_count")),
        dislikes=_int_or_none(eng.get("dislikes")),
        comments=_int_or_none(eng.get("comments") or ps.get("comment_count")),
        shares=_int_or_none(eng.get("shares")),
        subscriber_count=_int_or_none(ps.get("subscriber_count")),
        estimated_minutes_watched=_float_or_none(wt.get("estimated_minutes_watched")),
        average_view_duration=_float_or_none(wt.get("average_view_duration")),
        average_view_percentage=_float_or_none(wt.get("average_view_percentage")),
        estimated_revenue=_float_or_none(mon.get("estimated_revenue")),
        ad_impressions=_int_or_none(mon.get("ad_impressions")),
        cpm=_float_or_none(mon.get("cpm")),
        retention_at_ad_start=_float_or_none(metrics.get("retention_at_ad_start")),
        subscribers_gained=_int_or_none(eng.get("subscribers_gained")),
        subscribers_lost=_int_or_none(eng.get("subscribers_lost")),
    )


def _int_or_none(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _float_or_none(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_dt(val) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val:
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[CampaignRead])
async def list_campaigns(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Campaign]:
    result = await db.execute(
        select(Campaign).where(Campaign.owner_id == current_user.id)
    )
    return result.scalars().all()


@router.post("", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Campaign:
    logger.info(f"User {current_user.id} creating campaign: {payload.title}")
    campaign = Campaign(owner_id=current_user.id, title=payload.title, video_quantity=0)
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


@router.post(
    "/{id}/videos",
    response_model=VideoMetricRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_video_to_campaign(
    id: uuid.UUID,
    payload: VideoMetricCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoMetricRead:
    campaign = await db.get(Campaign, id)
    if campaign is None or campaign.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign was not found.",
        )

    try:
        parsed_video_id = extract_video_id_from_url(str(payload.youtube_url))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    existing_video = await db.scalar(
        select(VideoMetric).where(VideoMetric.video_id == parsed_video_id)
    )
    if existing_video:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This YouTube video already exists in the system.",
        )

    video_metric = VideoMetric(
        campaign_id=campaign.id,
        youtube_url=str(payload.youtube_url),
        video_id=parsed_video_id,
        ad_start_seconds=payload.ad_start_seconds,
        ad_end_seconds=payload.ad_end_seconds,
    )
    db.add(video_metric)

    # Increment denormalized counter
    campaign.video_quantity = (campaign.video_quantity or 0) + 1

    await db.commit()
    await db.refresh(video_metric)

    response = VideoMetricRead.model_validate(video_metric)
    response.oauth_login_url = f"/auth/youtube/login/{video_metric.video_id}"
    return response


@router.get("/{id}/dashboard", response_model=CampaignDashboardResponse)
async def get_campaign_dashboard(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CampaignDashboardResponse:
    campaign_id = id
    statement = (
        select(Campaign)
        .where(Campaign.id == campaign_id, Campaign.owner_id == current_user.id)
        .options(selectinload(Campaign.videos))
    )
    campaign = await db.scalar(statement)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign was not found.",
        )

    dashboard_items: list[DashboardVideoItem] = []

    for video in campaign.videos:
        try:
            logger.info(f"[DASHBOARD] Processing video: {video.video_id} (authorized={video.is_authorized})")
            metrics = await fetch_retention_data(
                video.video_id,
                db=db,
                preloaded_video=video,
            )
            logger.info(f"[DASHBOARD] ✅ Got metrics for {video.video_id}: views={metrics.get('public_stats', {}).get('view_count')}")
        except Exception as exc:
            logger.error(f"[DASHBOARD] ❌ EXCEPTION for video {video.video_id}: {type(exc).__name__}: {exc}", exc_info=True)
            metrics = {
                "public_stats": {},
                "metadata": {},
                "retention_points": [],
                "retention_at_ad_start": None,
            }

        dashboard_items.append(_build_dashboard_item(video, metrics))

    await db.commit()
    return CampaignDashboardResponse(
        campaign_id=campaign.id,
        title=campaign.title,
        videos=dashboard_items,
    )


@router.get("/{id}/videos/{video_id}/detail", response_model=VideoDetailResponse)
async def get_video_detail(
    id: uuid.UUID,
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoDetailResponse:
    """Full analytics detail for a single video."""
    campaign = await db.get(Campaign, id)
    if campaign is None or campaign.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign was not found.")

    video = await db.scalar(
        select(VideoMetric).where(
            VideoMetric.campaign_id == campaign.id,
            VideoMetric.video_id == video_id,
        )
    )
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video record was not found.")

    metrics = await fetch_retention_data(video.video_id, db=db, preloaded_video=video)
    await db.commit()
    return _build_detail_response(video, metrics)


@router.post("/{id}/compare", response_model=list[VideoComparisonItem])
async def compare_videos(
    id: uuid.UUID,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[VideoComparisonItem]:
    """Compare selected videos side-by-side."""
    campaign = await db.get(Campaign, id)
    if campaign is None or campaign.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign was not found.")

    video_ids = payload.get("video_ids", [])
    if not video_ids or not isinstance(video_ids, list):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="video_ids list is required.")

    result = await db.execute(
        select(VideoMetric).where(
            VideoMetric.campaign_id == campaign.id,
            VideoMetric.video_id.in_(video_ids),
        )
    )
    videos = result.scalars().all()

    comparison_items: list[VideoComparisonItem] = []
    for video in videos:
        try:
            metrics = await fetch_retention_data(video.video_id, db=db, preloaded_video=video)
        except Exception:
            metrics = {"public_stats": {}, "metadata": {}, "views_reach": {}, "watch_time": {}, "monetization": {}, "engagement": {}, "interactive": {}, "retention_at_ad_start": None}
        comparison_items.append(_build_comparison_item(video, metrics))

    await db.commit()
    return comparison_items


@router.post("/{id}/videos/{video_id}/sync", response_model=DashboardVideoItem)
async def sync_video_analytics(
    id: uuid.UUID,
    video_id: str,
    payload: dict | None = Body(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardVideoItem:
    campaign = await db.get(Campaign, id)
    if campaign is None or campaign.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign was not found.")

    video = await db.scalar(
        select(VideoMetric).where(
            VideoMetric.campaign_id == campaign.id,
            VideoMetric.video_id == video_id,
        )
    )
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video record was not found.")

    # Parse optional date range from body
    from datetime import date
    start_date = None
    end_date = None
    if isinstance(payload, dict):
        try:
            sd = payload.get("start_date")
            ed = payload.get("end_date")
            if sd:
                start_date = date.fromisoformat(sd)
            if ed:
                end_date = date.fromisoformat(ed)
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid date format. Use YYYY-MM-DD.")

    metrics = await fetch_retention_data(
        video.video_id,
        db=db,
        preloaded_video=video,
        force_refresh=True,
        start_date=start_date,
        end_date=end_date,
    )

    if not metrics.get("retention_points"):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "No data available for this date range. Try an older range."},
        )

    await db.commit()
    return _build_dashboard_item(video, metrics)
