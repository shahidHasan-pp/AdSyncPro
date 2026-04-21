"""
YouTube OAuth & URL utilities.
This module handles OAuth flow, URL parsing, and state encoding.
Analytics fetching has been consolidated into youtube_analytics.py.
"""

import json
import re
import base64
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import Flow

from app.core.config import get_settings

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
