"""
Videos routes — standalone video endpoints (cross-campaign).
"""

import uuid
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_current_user
from app.core.logger import get_logger
from app.models.campaign import Campaign
from app.models.user import User
from app.models.video_metric import VideoMetric
from app.schemas.video_metric import (
    DashboardVideoItem,
    PrivateMetrics,
    PublicMetrics,
    VideoDetailResponse,
)
from app.services.youtube_analytics import fetch_retention_data

# Re-use builder helpers from campaigns route
from app.api.routes.campaigns import (
    _build_dashboard_item,
    _build_detail_response,
)

router = APIRouter(prefix="/videos", tags=["Videos"])
logger = get_logger(__name__)


@router.get("", response_model=list[DashboardVideoItem])
async def list_all_videos(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DashboardVideoItem]:
    """List all videos across all campaigns for the authenticated user."""
    # Get all campaign IDs for this user
    campaigns = await db.execute(
        select(Campaign.id).where(Campaign.owner_id == current_user.id)
    )
    campaign_ids = [row[0] for row in campaigns.fetchall()]

    if not campaign_ids:
        return []

    result = await db.execute(
        select(VideoMetric).where(VideoMetric.campaign_id.in_(campaign_ids))
    )
    videos = result.scalars().all()

    items: list[DashboardVideoItem] = []
    for video in videos:
        try:
            metrics = await fetch_retention_data(video.video_id, db=db, preloaded_video=video)
        except Exception:
            metrics = {
                "public_stats": {},
                "metadata": {},
                "retention_points": [],
                "retention_at_ad_start": None,
            }
        items.append(_build_dashboard_item(video, metrics))

    await db.commit()
    return items


@router.get("/{video_id}", response_model=VideoDetailResponse)
async def get_video_detail_standalone(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoDetailResponse:
    """Full analytics detail for a single video (accessed from Videos page)."""
    # Verify video belongs to a campaign owned by the user
    video = await db.scalar(
        select(VideoMetric).where(VideoMetric.video_id == video_id)
    )
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found.")

    campaign = await db.get(Campaign, video.campaign_id)
    if campaign is None or campaign.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found.")

    metrics = await fetch_retention_data(video.video_id, db=db, preloaded_video=video)
    await db.commit()
    return _build_detail_response(video, metrics)


@router.post("/{video_id}/sync", response_model=DashboardVideoItem)
async def sync_video_standalone(
    video_id: str,
    payload: dict | None = Body(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardVideoItem:
    """Force sync analytics for a video (from Videos page)."""
    video = await db.scalar(
        select(VideoMetric).where(VideoMetric.video_id == video_id)
    )
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found.")

    campaign = await db.get(Campaign, video.campaign_id)
    if campaign is None or campaign.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found.")

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
            raise HTTPException(status_code=422, detail="Invalid date format.")

    metrics = await fetch_retention_data(
        video.video_id, db=db, preloaded_video=video, force_refresh=True,
        start_date=start_date, end_date=end_date,
    )

    if not metrics.get("retention_points"):
        return JSONResponse(
            status_code=200,
            content={"message": "No data available for this date range."},
        )

    await db.commit()
    return _build_dashboard_item(video, metrics)
