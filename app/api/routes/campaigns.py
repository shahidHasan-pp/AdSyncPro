import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status, Body
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_current_user
from app.core.logger import get_logger
from app.models.campaign import Campaign
from app.models.daily_stat import DailyStat
from app.models.user import User
from app.models.video_metric import VideoMetric
from app.schemas.campaign import CampaignCreate, CampaignDashboardResponse, CampaignRead
from app.schemas.video_metric import (
    DashboardVideoItem,
    PrivateMetrics,
    PublicMetrics,
    VideoMetricCreate,
    VideoMetricRead,
)
from app.services.youtube import extract_video_id_from_url
from app.services.youtube_analytics import fetch_retention_data

router = APIRouter(prefix="/campaigns", tags=["Campaign Management"])
logger = get_logger(__name__)


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
    campaign = Campaign(owner_id=current_user.id, title=payload.title)
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
    today = date.today()

    for video in campaign.videos:
        try:
            metrics = await fetch_retention_data(
                video.video_id,
                db=db,
                preloaded_video=video,
            )
        except Exception:
            metrics = {
                "total_views": None,
                "likes": None,
                "retention_at_start": None,
                "retention_points": [],
            }

        if metrics.get("total_views") is None and metrics.get("retention_at_start") is None:
            cached_stat = await db.scalar(
                select(DailyStat)
                .where(DailyStat.video_id == video.id)
                .order_by(DailyStat.date.desc())
                .limit(1)
            )
            if cached_stat is not None:
                metrics["total_views"] = cached_stat.total_views
                metrics["retention_at_start"] = cached_stat.retention_at_ad_start

        if metrics.get("total_views") is not None or metrics.get("retention_at_start") is not None:
            stat = await db.scalar(
                select(DailyStat).where(
                    DailyStat.video_id == video.id,
                    DailyStat.date == today,
                )
            )
            if stat is None:
                stat = DailyStat(
                    video_id=video.id,
                    date=today,
                    total_views=int(metrics.get("total_views") or 0),
                    retention_at_ad_start=(
                        float(metrics["retention_at_start"])
                        if metrics.get("retention_at_start") is not None
                        else None
                    ),
                )
                db.add(stat)
            else:
                if metrics.get("total_views") is not None:
                    stat.total_views = int(metrics["total_views"])
                if metrics.get("retention_at_start") is not None:
                    stat.retention_at_ad_start = float(metrics["retention_at_start"])

        dashboard_items.append(
            DashboardVideoItem(
                video_record_id=video.id,
                video_id=video.video_id,
                youtube_url=video.youtube_url,
                duration_seconds=(
                    int(metrics["duration_seconds"])
                    if metrics.get("duration_seconds") is not None
                    else None
                ),
                ad_start_seconds=video.ad_start_seconds,
                ad_end_seconds=video.ad_end_seconds,
                is_authorized=video.is_authorized,
                public_metrics=PublicMetrics(
                    total_views=(int(metrics["total_views"]) if metrics.get("total_views") is not None else None),
                    likes=(int(metrics["likes"]) if metrics.get("likes") is not None else None),
                    comments=(int(metrics["comments"]) if metrics.get("comments") is not None else None),
                    favorites=(int(metrics["favorites"]) if metrics.get("favorites") is not None else None),
                    upload_date=(
                        metrics["upload_date"]
                        if isinstance(metrics.get("upload_date"), datetime)
                        else (
                            datetime.fromisoformat(metrics["upload_date"].replace("Z", "+00:00"))
                            if isinstance(metrics.get("upload_date"), str) and metrics.get("upload_date")
                            else None
                        )
                    ),
                    subscriber_count=(
                        int(metrics["subscriber_count"])
                        if metrics.get("subscriber_count") is not None
                        else None
                    ),
                ),
                private_metrics=PrivateMetrics(
                    retention_at_start=(
                        float(metrics["retention_at_start"])
                        if metrics.get("retention_at_start") is not None
                        else None
                    ),
                    retention_at_ad_start=(
                        float(metrics["retention_at_start"])
                        if metrics.get("retention_at_start") is not None
                        else None
                    ),
                    retention_data=list(metrics.get("retention_points") or []),
                ),
            )
        )

    await db.commit()
    return CampaignDashboardResponse(
        campaign_id=campaign.id,
        title=campaign.title,
        videos=dashboard_items,
    )


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign was not found.",
        )

    video = await db.scalar(
        select(VideoMetric).where(
            VideoMetric.campaign_id == campaign.id,
            VideoMetric.video_id == video_id,
        )
    )
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video record was not found.",
        )

    # Parse optional date range from body
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

    # If Google returned no retention points, surface a clear message to the client
    if not metrics.get("retention_points"):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "No data available for this date range. Try an older range."},
        )

    await db.commit()

    return DashboardVideoItem(
        video_record_id=video.id,
        video_id=video.video_id,
        youtube_url=video.youtube_url,
        duration_seconds=(
            int(metrics["duration_seconds"])
            if metrics.get("duration_seconds") is not None
            else None
        ),
        ad_start_seconds=video.ad_start_seconds,
        ad_end_seconds=video.ad_end_seconds,
        is_authorized=video.is_authorized,
        public_metrics=PublicMetrics(
            total_views=(int(metrics["total_views"]) if metrics.get("total_views") is not None else None),
            likes=(int(metrics["likes"]) if metrics.get("likes") is not None else None),
            comments=(int(metrics["comments"]) if metrics.get("comments") is not None else None),
            favorites=(int(metrics["favorites"]) if metrics.get("favorites") is not None else None),
            upload_date=(
                metrics["upload_date"]
                if isinstance(metrics.get("upload_date"), datetime)
                else (
                    datetime.fromisoformat(metrics["upload_date"].replace("Z", "+00:00"))
                    if isinstance(metrics.get("upload_date"), str) and metrics.get("upload_date")
                    else None
                )
            ),
            subscriber_count=(
                int(metrics["subscriber_count"])
                if metrics.get("subscriber_count") is not None
                else None
            ),
        ),
        private_metrics=PrivateMetrics(
            retention_at_start=(
                float(metrics["retention_at_start"])
                if metrics.get("retention_at_start") is not None
                else None
            ),
            retention_at_ad_start=(
                float(metrics["retention_at_start"])
                if metrics.get("retention_at_start") is not None
                else None
            ),
            retention_data=list(metrics.get("retention_points") or []),
        ),
    )
