import asyncio
from datetime import datetime, timedelta, timezone, date

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import token_cipher
from app.models.video_metric import VideoMetric


CACHE_TTL = timedelta(minutes=60)


def _duration_to_seconds(duration: str) -> int:
    # ISO-8601 subset like PT1H2M3S
    import re

    pattern = re.compile(
        r"^PT"
        r"(?:(?P<hours>\d+)H)?"
        r"(?:(?P<minutes>\d+)M)?"
        r"(?:(?P<seconds>\d+)S)?$"
    )
    match = pattern.match(duration)
    if not match:
        return 0
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return (hours * 3600) + (minutes * 60) + seconds


def _load_google_client_config() -> tuple[str, str]:
    import json
    from pathlib import Path

    settings = get_settings()
    if settings.google_client_id and settings.google_client_secret:
        return settings.google_client_id, settings.google_client_secret

    secrets_path = Path(settings.google_client_secrets_file)
    with secrets_path.open("r", encoding="utf-8") as file:
        content = json.load(file)

    key = "web" if "web" in content else "installed"
    client_info = content[key]
    return client_info["client_id"], client_info["client_secret"]


def _build_credentials(refresh_token_encrypted: str) -> Credentials:
    settings = get_settings()
    client_id, client_secret = _load_google_client_config()
    decrypted_refresh_token = token_cipher.decrypt(refresh_token_encrypted)
    credentials = Credentials(
        token=None,
        refresh_token=decrypted_refresh_token,
        token_uri=settings.google_token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=settings.youtube_scopes,
    )
    credentials.refresh(Request())
    return credentials


def _nearest_retention(points: list[dict[str, float]], target_ratio: float | None) -> float | None:
    """Return the value closest to target_ratio. If target_ratio is None or points empty, return None."""
    if not points or target_ratio is None:
        return None
    nearest = min(points, key=lambda point: abs(point["ratio"] - target_ratio))
    return float(nearest["value"])


def fetch_public_stats(video_id: str, credentials: Credentials) -> dict:
    """
    YouTube Data API: youtube.videos().list(part="statistics,snippet,contentDetails").
    """
    youtube_data = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    response = (
        youtube_data.videos()
        .list(part="statistics,snippet,contentDetails", id=video_id)
        .execute()
    )
    items = response.get("items", [])
    if not items:
        return {
            "duration_seconds": None,
            "total_views": None,
            "likes": None,
            "comments": None,
            "favorites": None,
            "upload_date": None,
            "channel_id": None,
            "subscriber_count": None,
        }

    item = items[0]
    stats = item.get("statistics", {}) or {}
    snippet = item.get("snippet", {}) or {}
    content_details = item.get("contentDetails", {}) or {}

    duration_seconds = _duration_to_seconds(content_details.get("duration", "PT0S"))

    total_views = int(stats["viewCount"]) if "viewCount" in stats else None
    likes = int(stats["likeCount"]) if "likeCount" in stats else None
    comments = int(stats["commentCount"]) if "commentCount" in stats else None
    favorites = int(stats["favoriteCount"]) if "favoriteCount" in stats else None

    upload_date = snippet.get("publishedAt") if isinstance(snippet.get("publishedAt"), str) else None
    channel_id = snippet.get("channelId") if isinstance(snippet.get("channelId"), str) else None

    subscriber_count: int | None = None
    if channel_id:
        try:
            channel_resp = (
                youtube_data.channels()
                .list(part="statistics", id=channel_id)
                .execute()
            )
            channel_items = channel_resp.get("items", []) or []
            if channel_items:
                channel_stats = channel_items[0].get("statistics", {}) or {}
                if "subscriberCount" in channel_stats:
                    subscriber_count = int(channel_stats["subscriberCount"])
        except HttpError:
            subscriber_count = None

    return {
        "duration_seconds": duration_seconds if duration_seconds > 0 else None,
        "total_views": total_views,
        "likes": likes,
        "comments": comments,
        "favorites": favorites,
        "upload_date": upload_date,
        "channel_id": channel_id,
        "subscriber_count": subscriber_count,
    }


def _fetch_retention_points_from_google(
    video_id: str,
    credentials: Credentials,
    *,
    start_date: date,
    end_date: date,
) -> list[dict[str, float]]:
    analytics = build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)
    report = (
        analytics.reports()
        .query(
            ids="channel==MINE",
            startDate=start_date.isoformat(),
            endDate=end_date.isoformat(),
            metrics="audienceWatchRatio,relativeRetentionPerformance",
            dimensions="elapsedVideoTimeRatio",
            filters=f"video=={video_id}",
            sort="elapsedVideoTimeRatio",
        )
        .execute()
    )
    rows = report.get("rows", []) or []
    points: list[dict[str, float]] = []
    for row in rows:
        if not row or len(row) < 2:
            continue
        points.append({"ratio": float(row[0]), "value": float(row[1])})
    return points


def _fetch_video_metrics_from_google(video: VideoMetric, *, start_date: date | None = None, end_date: date | None = None) -> dict:
    """
    Fetches public stats (Data API) + retention points (Analytics API) using a single refreshed credential.
    Returns a cache blob (never includes refresh tokens).

    Accepts optional start_date/end_date to control the Analytics API window. If not provided,
    defaults to the last `retention_lookback_days` ending 2 days ago.
    """
    now = datetime.now(timezone.utc)
    if not video.is_authorized or not video.refresh_token:
        return {
            "fetched_at": now.isoformat(),
            "duration_seconds": None,
            "total_views": None,
            "likes": None,
            "comments": None,
            "favorites": None,
            "upload_date": None,
            "subscriber_count": None,
            "retention_points": [],
        }

    credentials = _build_credentials(video.refresh_token)
    public = fetch_public_stats(video.video_id, credentials)

    settings = get_settings()
    today = date.today()

    published_date: date | None = None
    upload_date = public.get("upload_date")
    if isinstance(upload_date, str) and upload_date:
        try:
            published_date = datetime.fromisoformat(upload_date.replace("Z", "+00:00")).date()
        except ValueError:
            published_date = None

    # If user provided neither start_date nor end_date, request full range from upload -> today when available.
    if start_date is None and end_date is None:
        if published_date:
            start_date = published_date
            end_date = today
        else:
            # Fallback to the default lookback ending today
            end_date = today
            lookback_start = end_date - timedelta(days=settings.retention_lookback_days - 1)
            start_date = lookback_start
    else:
        # Normalize partial inputs: default end_date to today if missing
        if end_date is None:
            end_date = today
        # If start_date still missing, use either published_date (not earlier than lookback) or lookback window
        lookback_start = end_date - timedelta(days=settings.retention_lookback_days - 1)
        if start_date is None:
            start_date = max(lookback_start, published_date) if published_date else lookback_start

    retention_points: list[dict[str, float]] = []
    retention_error: str | None = None
    try:
        retention_points = _fetch_retention_points_from_google(
            video.video_id,
            credentials,
            start_date=start_date,
            end_date=end_date,
        )
    except HttpError as exc:
        retention_points = []
        retention_error = str(exc)

    return {
        "fetched_at": now.isoformat(),
        "duration_seconds": public.get("duration_seconds"),
        "total_views": public.get("total_views"),
        "likes": public.get("likes"),
        "comments": public.get("comments"),
        "favorites": public.get("favorites"),
        "upload_date": public.get("upload_date"),
        "subscriber_count": public.get("subscriber_count"),
        "retention_points": retention_points,
        "retention_error": retention_error,
    }


async def get_video_by_video_id(
    db: AsyncSession, video_id: str, preloaded_video: VideoMetric | None = None
) -> VideoMetric:
    video = preloaded_video
    if video is None:
        video = await db.scalar(select(VideoMetric).where(VideoMetric.video_id == video_id))
    if video is None:
        raise ValueError(f"Video with video_id '{video_id}' was not found.")
    return video


async def fetch_retention_data(
    video_id: str,
    db: AsyncSession,
    *,
    preloaded_video: VideoMetric | None = None,
    force_refresh: bool = False,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """
    Returns a dict containing:
      - total_views, likes, comments, favorites, upload_date, subscriber_count
      - duration_seconds
      - retention_points: list[{ratio,value}]
      - retention_at_start: float | None
      - cached: bool
    """
    video = await get_video_by_video_id(db, video_id, preloaded_video=preloaded_video)

    now = datetime.now(timezone.utc)
    last_updated = video.last_updated
    if last_updated is not None and last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=timezone.utc)
    if (
        not force_refresh
        and last_updated is not None
        and video.retention_json is not None
        and (now - last_updated) < CACHE_TTL
    ):
        cache = video.retention_json
        points = list(cache.get("retention_points") or [])
        duration_seconds = cache.get("duration_seconds")
        if isinstance(duration_seconds, int) and duration_seconds > 0 and isinstance(video.ad_start_seconds, (int, float)):
            target_ratio = float(video.ad_start_seconds) / float(duration_seconds)
        else:
            target_ratio = None
        return {
            "total_views": cache.get("total_views"),
            "likes": cache.get("likes"),
            "comments": cache.get("comments"),
            "favorites": cache.get("favorites"),
            "upload_date": cache.get("upload_date"),
            "subscriber_count": cache.get("subscriber_count"),
            "duration_seconds": duration_seconds,
            "retention_points": points,
            "retention_at_start": _nearest_retention(points, target_ratio),
            "cached": True,
        }

    # Fetch using optional date range
    cache_blob = await asyncio.to_thread(_fetch_video_metrics_from_google, video, start_date=start_date, end_date=end_date)
    video.retention_json = cache_blob
    video.last_updated = now
    video.comment_count = cache_blob.get("comments")
    video.subscriber_count = cache_blob.get("subscriber_count")

    points = list(cache_blob.get("retention_points") or [])
    duration_seconds = cache_blob.get("duration_seconds")
    # Only calculate target_ratio if both duration and ad_start_seconds are available and valid
    if isinstance(duration_seconds, int) and duration_seconds > 0 and isinstance(video.ad_start_seconds, (int, float)):
        target_ratio = float(video.ad_start_seconds) / float(duration_seconds)
    else:
        target_ratio = None

    return {
        "total_views": cache_blob.get("total_views"),
        "likes": cache_blob.get("likes"),
        "comments": cache_blob.get("comments"),
        "favorites": cache_blob.get("favorites"),
        "upload_date": cache_blob.get("upload_date"),
        "subscriber_count": cache_blob.get("subscriber_count"),
        "duration_seconds": duration_seconds,
        "retention_points": points,
        "retention_at_start": _nearest_retention(points, target_ratio),
        "cached": False,
    }
