"""
YouTube Analytics Service — Full Metrics
Fetches ALL available metrics from YouTube Data API v3 + YouTube Analytics API v2,
aligned with gcp_script.py metric set.
"""

import asyncio
import json as json_module
import re
from datetime import datetime, timedelta, timezone, date

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logger import get_logger
from app.core.security import token_cipher
from app.models.video_metric import VideoMetric

logger = get_logger(__name__)

CACHE_TTL = timedelta(minutes=60)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _duration_to_seconds(duration: str) -> int:
    """Convert ISO 8601 duration (PT1H2M3S) to seconds."""
    pattern = re.compile(
        r"^PT"
        r"(?:(?P<hours>\d+)H)?"
        r"(?:(?P<minutes>\d+)M)?"
        r"(?:(?P<seconds>\d+)S)?$"
    )
    match = pattern.match(duration)
    if not match:
        logger.warning(f"[DURATION] Could not parse duration: '{duration}'")
        return 0
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    result = (hours * 3600) + (minutes * 60) + seconds
    logger.info(f"[DURATION] Parsed '{duration}' -> {result}s")
    return result


def _load_google_client_config() -> tuple[str, str]:
    from pathlib import Path

    settings = get_settings()
    if settings.google_client_id and settings.google_client_secret:
        logger.info("[CONFIG] Using client_id/secret from settings (env vars)")
        return settings.google_client_id, settings.google_client_secret

    secrets_path = Path(settings.google_client_secrets_file)
    logger.info(f"[CONFIG] Loading client secrets from file: {secrets_path.absolute()}")
    if not secrets_path.exists():
        logger.error(f"[CONFIG] Client secrets file NOT FOUND: {secrets_path.absolute()}")
        raise FileNotFoundError(f"Client secrets file not found: {secrets_path.absolute()}")

    with secrets_path.open("r", encoding="utf-8") as file:
        content = json_module.load(file)

    key = "web" if "web" in content else "installed"
    client_info = content[key]
    logger.info(f"[CONFIG] Loaded client_id: {client_info['client_id'][:20]}...")
    return client_info["client_id"], client_info["client_secret"]


def _build_credentials(refresh_token_encrypted: str) -> Credentials:
    settings = get_settings()
    client_id, client_secret = _load_google_client_config()

    logger.info(f"[AUTH] Decrypting refresh token (len={len(refresh_token_encrypted)})")
    decrypted_refresh_token = token_cipher.decrypt(refresh_token_encrypted)
    logger.info(f"[AUTH] Decrypted refresh token (len={len(decrypted_refresh_token)}), first 10: {decrypted_refresh_token[:10]}...")

    credentials = Credentials(
        token=None,
        refresh_token=decrypted_refresh_token,
        token_uri=settings.google_token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=settings.youtube_scopes,
    )
    logger.info(f"[AUTH] Refreshing credentials with scopes: {settings.youtube_scopes}")
    try:
        credentials.refresh(Request())
        logger.info(f"[AUTH] ✅ Credentials refreshed successfully. Token valid: {credentials.valid}, expired: {credentials.expired}")
    except Exception as exc:
        logger.error(f"[AUTH] ❌ Failed to refresh credentials: {type(exc).__name__}: {exc}")
        raise
    return credentials


def _nearest_retention(points: list[dict[str, float]], target_ratio: float | None) -> float | None:
    """Return the value closest to target_ratio."""
    if not points or target_ratio is None:
        return None
    nearest = min(points, key=lambda point: abs(point["ratio"] - target_ratio))
    return float(nearest["value"])


# ---------------------------------------------------------------------------
# YouTube Data API: Public stats + metadata
# ---------------------------------------------------------------------------

def _fetch_public_stats(video_id: str, credentials: Credentials) -> dict:
    """
    YouTube Data API: videos().list(part="snippet,contentDetails,statistics,status").
    Returns metadata + public stats + channel subscriber count.
    """
    logger.info(f"[DATA API] Fetching public stats for video_id={video_id}")
    youtube_data = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    response = (
        youtube_data.videos()
        .list(part="snippet,contentDetails,statistics,status", id=video_id)
        .execute()
    )

    logger.info(f"[DATA API] Raw response keys: {list(response.keys())}")
    items = response.get("items", [])
    logger.info(f"[DATA API] Got {len(items)} items")

    if not items:
        logger.warning(f"[DATA API] ⚠️ No items returned for video_id={video_id}")
        return {
            "metadata": {},
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
    status = item.get("status", {}) or {}

    logger.info(f"[DATA API] statistics: {json_module.dumps(stats, default=str)}")
    logger.info(f"[DATA API] snippet.title: {snippet.get('title')}")
    logger.info(f"[DATA API] snippet.channelTitle: {snippet.get('channelTitle')}")
    logger.info(f"[DATA API] contentDetails.duration: {content_details.get('duration')}")
    logger.info(f"[DATA API] status.privacyStatus: {status.get('privacyStatus')}")

    duration_seconds = _duration_to_seconds(content_details.get("duration", "PT0S"))

    total_views = int(stats["viewCount"]) if "viewCount" in stats else None
    likes_count = int(stats["likeCount"]) if "likeCount" in stats else None
    comments_count = int(stats["commentCount"]) if "commentCount" in stats else None
    favorites = int(stats["favoriteCount"]) if "favoriteCount" in stats else None

    logger.info(f"[DATA API] Parsed: views={total_views}, likes={likes_count}, comments={comments_count}, favorites={favorites}")

    upload_date = snippet.get("publishedAt") if isinstance(snippet.get("publishedAt"), str) else None
    channel_id = snippet.get("channelId") if isinstance(snippet.get("channelId"), str) else None
    logger.info(f"[DATA API] upload_date={upload_date}, channel_id={channel_id}")

    # Fetch channel subscriber count
    subscriber_count: int | None = None
    if channel_id:
        try:
            logger.info(f"[DATA API] Fetching channel stats for channel_id={channel_id}")
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
                    logger.info(f"[DATA API] Channel subscriber_count={subscriber_count}")
                else:
                    logger.warning(f"[DATA API] No subscriberCount in channel stats")
            else:
                logger.warning(f"[DATA API] No channel items returned")
        except HttpError as exc:
            logger.error(f"[DATA API] ❌ Channel stats fetch failed: {exc}")
            subscriber_count = None

    metadata = {
        "title": snippet.get("title"),
        "description": (snippet.get("description", "") or "")[:200],
        "channel_id": channel_id,
        "channel_title": snippet.get("channelTitle"),
        "duration_seconds": duration_seconds if duration_seconds > 0 else None,
        "published_at": upload_date,
        "privacy_status": status.get("privacyStatus"),
    }

    result = {
        "metadata": metadata,
        "duration_seconds": duration_seconds if duration_seconds > 0 else None,
        "total_views": total_views,
        "likes": likes_count,
        "comments": comments_count,
        "favorites": favorites,
        "upload_date": upload_date,
        "channel_id": channel_id,
        "subscriber_count": subscriber_count,
    }
    logger.info(f"[DATA API] ✅ Final public stats result: {json_module.dumps(result, default=str)}")
    return result


# ---------------------------------------------------------------------------
# YouTube Analytics API: Full analytics report
# ---------------------------------------------------------------------------

def _fetch_analytics_report(
    video_id: str,
    credentials: Credentials,
    *,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Fetch ALL available analytics metrics for a single video.
    Aligned with gcp_script.py metric categories.
    """
    logger.info(f"[ANALYTICS] Fetching analytics report for video_id={video_id}, range={start_date} to {end_date}")
    analytics = build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)

    # Core + monetization metrics
    all_metrics = (
        "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
        "likes,dislikes,comments,shares,"
        "subscribersGained,subscribersLost,"
        "estimatedRevenue,estimatedAdRevenue,adImpressions,monetizedPlaybacks,"
        "playbackBasedCpm,cpm"
    )

    logger.info(f"[ANALYTICS] Requesting metrics: {all_metrics}")

    analytics_data: dict = {}
    try:
        report = (
            analytics.reports()
            .query(
                ids="channel==MINE",
                startDate=start_date.isoformat(),
                endDate=end_date.isoformat(),
                metrics=all_metrics,
                dimensions="video",
                filters=f"video=={video_id}",
                sort="-views",
                maxResults=1,
            )
            .execute()
        )
        logger.info(f"[ANALYTICS] Raw report keys: {list(report.keys())}")
        logger.info(f"[ANALYTICS] Column headers: {report.get('columnHeaders', [])}")
        rows = report.get("rows", [])
        logger.info(f"[ANALYTICS] Got {len(rows)} rows")
        if rows:
            headers = [h["name"] for h in report["columnHeaders"]]
            analytics_data = dict(zip(headers, rows[0]))
            logger.info(f"[ANALYTICS] ✅ Analytics data: {json_module.dumps(analytics_data, default=str)}")
        else:
            logger.warning(f"[ANALYTICS] ⚠️ No rows returned for video_id={video_id}")
    except HttpError as exc:
        logger.error(f"[ANALYTICS] ❌ Analytics report failed for {video_id}: {exc}")
        logger.error(f"[ANALYTICS] ❌ HTTP status: {exc.resp.status if hasattr(exc, 'resp') else 'unknown'}")
        logger.error(f"[ANALYTICS] ❌ Content: {exc.content if hasattr(exc, 'content') else 'unknown'}")
    except Exception as exc:
        logger.error(f"[ANALYTICS] ❌ Unexpected error: {type(exc).__name__}: {exc}")

    return analytics_data


# ---------------------------------------------------------------------------
# YouTube Analytics API: Retention points
# ---------------------------------------------------------------------------

def _fetch_retention_points(
    video_id: str,
    credentials: Credentials,
    *,
    start_date: date,
    end_date: date,
) -> list[dict[str, float]]:
    """Fetch audience retention curve (elapsedVideoTimeRatio × audienceWatchRatio)."""
    logger.info(f"[RETENTION] Fetching retention points for video_id={video_id}, range={start_date} to {end_date}")
    analytics = build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)
    try:
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
        logger.info(f"[RETENTION] Got {len(rows)} retention points")
        if rows and len(rows) > 0:
            logger.info(f"[RETENTION] First point: {rows[0]}")
            logger.info(f"[RETENTION] Last point: {rows[-1]}")
    except HttpError as exc:
        logger.error(f"[RETENTION] ❌ Retention fetch failed: {exc}")
        logger.error(f"[RETENTION] ❌ HTTP status: {exc.resp.status if hasattr(exc, 'resp') else 'unknown'}")
        logger.error(f"[RETENTION] ❌ Content: {exc.content if hasattr(exc, 'content') else 'unknown'}")
        raise

    points: list[dict[str, float]] = []
    for row in rows:
        if not row or len(row) < 2:
            continue
        points.append({"ratio": float(row[0]), "value": float(row[1])})

    logger.info(f"[RETENTION] ✅ Parsed {len(points)} retention points")
    return points


# ---------------------------------------------------------------------------
# Combined fetch: builds the full retention_json blob
# ---------------------------------------------------------------------------

def _fetch_video_metrics_from_google(
    video: VideoMetric,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """
    Fetches public stats (Data API) + full analytics report + retention points.
    Returns the complete cache blob to store in retention_json.
    """
    logger.info(f"[FETCH] === Starting full metrics fetch for video_id={video.video_id} ===")
    logger.info(f"[FETCH] is_authorized={video.is_authorized}, has_refresh_token={bool(video.refresh_token)}")

    now = datetime.now(timezone.utc)
    if not video.is_authorized or not video.refresh_token:
        logger.warning(f"[FETCH] ⚠️ Video not authorized or no refresh token - returning empty blob")
        return {
            "fetched_at": now.isoformat(),
            "metadata": {},
            "views_reach": {},
            "watch_time": {},
            "monetization": {},
            "engagement": {},
            "interactive": {},
            "retention_points": [],
            "public_stats": {},
        }

    try:
        credentials = _build_credentials(video.refresh_token)
    except Exception as exc:
        logger.error(f"[FETCH] ❌ Failed to build credentials: {type(exc).__name__}: {exc}")
        return {
            "fetched_at": now.isoformat(),
            "metadata": {},
            "views_reach": {},
            "watch_time": {},
            "monetization": {},
            "engagement": {},
            "interactive": {},
            "retention_points": [],
            "public_stats": {},
            "error": f"Credential error: {exc}",
        }

    # 1. Public stats + metadata
    try:
        public = _fetch_public_stats(video.video_id, credentials)
        logger.info(f"[FETCH] ✅ Public stats fetched successfully")
    except Exception as exc:
        logger.error(f"[FETCH] ❌ Public stats fetch failed: {type(exc).__name__}: {exc}")
        public = {
            "metadata": {},
            "duration_seconds": None,
            "total_views": None,
            "likes": None,
            "comments": None,
            "favorites": None,
            "upload_date": None,
            "channel_id": None,
            "subscriber_count": None,
        }

    # 2. Determine date range
    settings = get_settings()
    today = date.today()
    lag_days = max(0, settings.youtube_analytics_data_lag_days)
    latest_available_date = today - timedelta(days=lag_days)

    published_date: date | None = None
    upload_date_str = public.get("upload_date")
    if isinstance(upload_date_str, str) and upload_date_str:
        try:
            published_date = datetime.fromisoformat(upload_date_str.replace("Z", "+00:00")).date()
        except ValueError:
            published_date = None

    if start_date is None and end_date is None:
        end_date = latest_available_date
        if published_date:
            start_date = min(published_date, end_date)
        else:
            lookback_days = max(1, settings.retention_lookback_days)
            start_date = end_date - timedelta(days=lookback_days - 1)
    else:
        if end_date is None:
            end_date = latest_available_date
        end_date = min(end_date, latest_available_date)
        if start_date is None:
            lookback_days = max(1, settings.retention_lookback_days)
            lookback_start = end_date - timedelta(days=lookback_days - 1)
            start_date = max(lookback_start, published_date) if published_date else lookback_start
        elif start_date > end_date:
            start_date = end_date

    if start_date > end_date:
        start_date = end_date

    logger.info(f"[FETCH] Date range: {start_date} to {end_date} (published={published_date}, lag={lag_days})")

    # 3. Full analytics report
    try:
        analytics_data = _fetch_analytics_report(
            video.video_id, credentials, start_date=start_date, end_date=end_date
        )
        logger.info(f"[FETCH] ✅ Analytics report: {len(analytics_data)} metrics returned")
    except Exception as exc:
        logger.error(f"[FETCH] ❌ Analytics report failed: {type(exc).__name__}: {exc}")
        analytics_data = {}

    # 4. Retention curve
    retention_points: list[dict[str, float]] = []
    try:
        retention_points = _fetch_retention_points(
            video.video_id, credentials, start_date=start_date, end_date=end_date
        )
        logger.info(f"[FETCH] ✅ Retention: {len(retention_points)} points")
    except HttpError as exc:
        logger.error(f"[FETCH] ❌ Retention fetch failed: {type(exc).__name__}: {exc}")
    except Exception as exc:
        logger.error(f"[FETCH] ❌ Retention unexpected error: {type(exc).__name__}: {exc}")

    # 5. Calculate retention at ad start
    duration_seconds = public.get("duration_seconds")
    if isinstance(duration_seconds, int) and duration_seconds > 0:
        target_ratio = float(video.ad_start_seconds) / float(duration_seconds)
    else:
        target_ratio = None
    retention_at_ad_start = _nearest_retention(retention_points, target_ratio)
    logger.info(f"[FETCH] Retention at ad start: {retention_at_ad_start} (target_ratio={target_ratio})")

    # 6. Build structured blob
    metadata = public.get("metadata", {})

    final_blob = {
        "fetched_at": now.isoformat(),
        "metadata": metadata,
        "views_reach": {
            "views": analytics_data.get("views") or public.get("total_views"),
            "uniques": analytics_data.get("uniques"),
            "engaged_views": analytics_data.get("engagedViews"),
            "thumbnail_impressions": analytics_data.get("videoThumbnailImpressions"),
            "thumbnail_ctr": analytics_data.get("videoThumbnailImpressionsClickRate"),
        },
        "watch_time": {
            "estimated_minutes_watched": analytics_data.get("estimatedMinutesWatched"),
            "average_view_duration": analytics_data.get("averageViewDuration"),
            "average_view_percentage": analytics_data.get("averageViewPercentage"),
        },
        "monetization": {
            "estimated_revenue": analytics_data.get("estimatedRevenue"),
            "estimated_ad_revenue": analytics_data.get("estimatedAdRevenue"),
            "ad_impressions": analytics_data.get("adImpressions"),
            "monetized_playbacks": analytics_data.get("monetizedPlaybacks"),
            "playback_based_cpm": analytics_data.get("playbackBasedCpm"),
            "cpm": analytics_data.get("cpm"),
        },
        "engagement": {
            "likes": analytics_data.get("likes") or public.get("likes"),
            "dislikes": analytics_data.get("dislikes"),
            "comments": analytics_data.get("comments") or public.get("comments"),
            "shares": analytics_data.get("shares"),
            "subscribers_gained": analytics_data.get("subscribersGained"),
            "subscribers_lost": analytics_data.get("subscribersLost"),
        },
        "interactive": {
            "cards_impressions": analytics_data.get("cardImpressions"),
            "cards_click_rate": analytics_data.get("cardClickRate"),
            "end_screen_click_rate": analytics_data.get("endScreenClickRate"),
        },
        "retention_points": retention_points,
        "retention_at_ad_start": retention_at_ad_start,
        "public_stats": {
            "view_count": public.get("total_views"),
            "like_count": public.get("likes"),
            "comment_count": public.get("comments"),
            "favorite_count": public.get("favorites"),
            "subscriber_count": public.get("subscriber_count"),
            "upload_date": public.get("upload_date"),
            "duration_seconds": public.get("duration_seconds"),
        },
    }

    logger.info(f"[FETCH] === FINAL BLOB SUMMARY ===")
    logger.info(f"[FETCH] metadata.title: {metadata.get('title')}")
    logger.info(f"[FETCH] views_reach.views: {final_blob['views_reach'].get('views')}")
    logger.info(f"[FETCH] engagement.likes: {final_blob['engagement'].get('likes')}")
    logger.info(f"[FETCH] retention_points count: {len(retention_points)}")
    logger.info(f"[FETCH] retention_at_ad_start: {retention_at_ad_start}")
    logger.info(f"[FETCH] public_stats: {json_module.dumps(final_blob['public_stats'], default=str)}")

    return final_blob


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

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
    Returns the full analytics blob. Uses caching via retention_json.
    Also updates top-level denormalized columns on VideoMetric.
    """
    video = await get_video_by_video_id(db, video_id, preloaded_video=preloaded_video)

    logger.info(f"[CACHE] fetch_retention_data called for video_id={video_id}, force_refresh={force_refresh}")
    logger.info(f"[CACHE] Video DB state: is_authorized={video.is_authorized}, has_refresh_token={bool(video.refresh_token)}, has_retention_json={video.retention_json is not None}, last_updated={video.last_updated}")

    now = datetime.now(timezone.utc)
    last_updated = video.last_updated
    if last_updated is not None and last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=timezone.utc)

    # Return cache if fresh enough
    if (
        not force_refresh
        and last_updated is not None
        and video.retention_json is not None
        and (now - last_updated) < CACHE_TTL
    ):
        logger.info(f"[CACHE] ✅ Returning cached data (age={(now - last_updated).seconds}s)")
        cache = video.retention_json
        points = list(cache.get("retention_points") or [])
        ps = cache.get("public_stats", {})
        duration_seconds = ps.get("duration_seconds")
        if isinstance(duration_seconds, int) and duration_seconds > 0:
            target_ratio = float(video.ad_start_seconds) / float(duration_seconds)
        else:
            target_ratio = None

        return {
            **cache,
            "retention_at_ad_start": _nearest_retention(points, target_ratio),
            "cached": True,
        }

    logger.info(f"[CACHE] Cache miss or force_refresh - fetching fresh data from Google APIs")

    # Fetch fresh data
    cache_blob = await asyncio.to_thread(
        _fetch_video_metrics_from_google, video, start_date=start_date, end_date=end_date
    )

    logger.info(f"[DB] Persisting data to video_metrics table...")

    # Persist to DB
    video.retention_json = cache_blob
    video.last_updated = now

    # Denormalize top-level columns
    metadata = cache_blob.get("metadata", {})
    ps = cache_blob.get("public_stats", {})
    engagement = cache_blob.get("engagement", {})

    video.title = metadata.get("title")
    video.channel_title = metadata.get("channel_title")
    video.duration_seconds = ps.get("duration_seconds")
    video.total_views = ps.get("view_count")
    video.likes = ps.get("like_count") or engagement.get("likes")
    video.comment_count = ps.get("comment_count") or engagement.get("comments")
    video.subscriber_count = ps.get("subscriber_count")

    logger.info(f"[DB] Denormalized: title={video.title}, views={video.total_views}, likes={video.likes}, comments={video.comment_count}, subs={video.subscriber_count}")

    upload_date_str = ps.get("upload_date")
    if isinstance(upload_date_str, str) and upload_date_str:
        try:
            video.upload_date = datetime.fromisoformat(upload_date_str.replace("Z", "+00:00"))
            logger.info(f"[DB] upload_date parsed: {video.upload_date}")
        except ValueError as exc:
            logger.warning(f"[DB] Failed to parse upload_date '{upload_date_str}': {exc}")

    logger.info(f"[DB] ✅ Data set on ORM object. Awaiting db.commit() from caller.")

    return {
        **cache_blob,
        "cached": False,
    }
