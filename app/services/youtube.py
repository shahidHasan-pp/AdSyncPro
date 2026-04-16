import asyncio
import json
import re
import base64
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import token_cipher
from app.models.video_metric import VideoMetric

YOUTUBE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def extract_video_id_from_url(youtube_url: str) -> str:
    parsed = urlparse(youtube_url)
    host = parsed.netloc.lower()

    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
            candidate = parsed.path.strip("/").split("/")[1]
        else:
            candidate = ""
    else:
        candidate = ""

    if not YOUTUBE_ID_PATTERN.match(candidate):
        raise ValueError("Invalid YouTube URL. Could not extract a valid 11-character video ID.")
    return candidate


def build_oauth_flow(state: str | None = None) -> Flow:
    settings = get_settings()
    flow = Flow.from_client_secrets_file(
        settings.google_client_secrets_file,
        scopes=settings.youtube_scopes,
        state=state,
    )
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def generate_auth_url(state: str) -> tuple[str, str]:
    flow = build_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url, state


def encode_oauth_state(video_id: str, redirect_url: str | None = None) -> str:
    payload: dict[str, str] = {"video_id": video_id}
    if redirect_url:
        payload["redirect_url"] = redirect_url
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_oauth_state(state: str) -> tuple[str, str | None]:
    """
    Supports legacy state=video_id as well as base64url JSON payload.
    Returns (video_id, redirect_url).
    """
    if YOUTUBE_ID_PATTERN.match(state):
        return state, None

    padded = state + "=" * (-len(state) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid OAuth state parameter.") from exc

    video_id = payload.get("video_id")
    redirect_url = payload.get("redirect_url")
    if not isinstance(video_id, str) or not YOUTUBE_ID_PATTERN.match(video_id):
        raise ValueError("Invalid OAuth state payload.")
    if redirect_url is not None and not isinstance(redirect_url, str):
        raise ValueError("Invalid redirect URL in OAuth state.")
    return video_id, redirect_url


def _load_google_client_config() -> tuple[str, str]:
    settings = get_settings()
    if settings.google_client_id and settings.google_client_secret:
        return settings.google_client_id, settings.google_client_secret

    secrets_path = Path(settings.google_client_secrets_file)
    with secrets_path.open("r", encoding="utf-8") as file:
        content = json.load(file)

    key = "web" if "web" in content else "installed"
    client_info = content[key]
    return client_info["client_id"], client_info["client_secret"]


def _duration_to_seconds(duration: str) -> int:
    # ISO-8601 subset like PT1H2M3S
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


def _fetch_youtube_analytics_for_video(video: VideoMetric) -> dict[str, float | int | None]:
    """
    Fetches public stats and audience retention data for a stored video record.
    Uses reports.query with audienceRetentionPercentage for retention insight.
    """
    default_payload: dict[str, float | int | None] = {
        "total_views": None,
        "likes": None,
        "retention_at_ad_start": None,
    }

    if not video.is_authorized or not video.refresh_token:
        return default_payload

    settings = get_settings()
    client_id, client_secret = _load_google_client_config()
    decrypted_refresh_token = token_cipher.decrypt(video.refresh_token)

    credentials = Credentials(
        token=None,
        refresh_token=decrypted_refresh_token,
        token_uri=settings.google_token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=settings.youtube_scopes,
    )

    youtube_data = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    metadata_response = (
        youtube_data.videos()
        .list(part="statistics,contentDetails", id=video.video_id)
        .execute()
    )

    items = metadata_response.get("items", [])
    if not items:
        return default_payload

    stats = items[0].get("statistics", {})
    content_details = items[0].get("contentDetails", {})
    duration_seconds = _duration_to_seconds(content_details.get("duration", "PT0S"))

    default_payload["total_views"] = int(stats["viewCount"]) if "viewCount" in stats else None
    default_payload["likes"] = int(stats["likeCount"]) if "likeCount" in stats else None

    analytics = build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)
    end_date = date.today()
    start_date = end_date - timedelta(days=settings.retention_lookback_days)
    report = (
        analytics.reports()
        .query(
            ids="channel==MINE",
            startDate=start_date.isoformat(),
            endDate=end_date.isoformat(),
            metrics="audienceRetentionPercentage",
            dimensions="elapsedVideoTimeRatio",
            filters=f"video=={video.video_id}",
            sort="elapsedVideoTimeRatio",
        )
        .execute()
    )

    rows = report.get("rows", [])
    if not rows:
        return default_payload

    target_ratio = (video.ad_start_seconds / duration_seconds) if duration_seconds else 0.0
    nearest = min(rows, key=lambda row: abs(float(row[0]) - target_ratio))
    default_payload["retention_at_ad_start"] = float(nearest[1])
    return default_payload


async def fetch_youtube_analytics(
    video_id: str,
    db: AsyncSession,
    preloaded_video: VideoMetric | None = None,
) -> dict[str, float | int | None]:
    """
    Required analytics engine entrypoint.
    Resolves a video by video_id, decrypts the stored refresh token, then fetches
    audienceRetentionPercentage via YouTube Analytics API.
    """
    video = preloaded_video
    if video is None:
        video = await db.scalar(select(VideoMetric).where(VideoMetric.video_id == video_id))

    if video is None:
        raise ValueError(f"Video with video_id '{video_id}' was not found.")

    return await asyncio.to_thread(_fetch_youtube_analytics_for_video, video)
