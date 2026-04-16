from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import token_cipher
from app.db.session import get_db_session
from app.models.video_metric import VideoMetric
from app.schemas.auth import OAuthCallbackResponse, OAuthLoginResponse
from app.services.youtube import (
    build_oauth_flow,
    decode_oauth_state,
    encode_oauth_state,
    generate_auth_url,
)

router = APIRouter(prefix="/auth/youtube", tags=["Google OAuth"])


@router.get("/login/{video_id}", response_model=OAuthLoginResponse)
async def youtube_login(
    video_id: str,
    redirect_url: str | None = Query(
        default=None,
        description="Optional URL to redirect to after OAuth callback (must be http://localhost:5173/...)",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> OAuthLoginResponse:
    video = await db.scalar(select(VideoMetric).where(VideoMetric.video_id == video_id))
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video record was not found.",
        )

    if redirect_url:
        # Allow any http or https redirect (useful when frontend is deployed on a remote host or IP)
        from urllib.parse import urlparse

        try:
            parsed = urlparse(redirect_url)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid redirect_url",
            )
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="redirect_url must be an absolute http or https URL",
            )

    state_payload = encode_oauth_state(video_id=video_id, redirect_url=redirect_url)
    auth_url, state = generate_auth_url(state_payload)
    return OAuthLoginResponse(auth_url=auth_url, state=state)


@router.get("/callback", response_model=OAuthCallbackResponse)
async def youtube_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="State parameter containing video_id"),
    db: AsyncSession = Depends(get_db_session),
) -> OAuthCallbackResponse:
    try:
        video_id, redirect_url = decode_oauth_state(state)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    video = await db.scalar(select(VideoMetric).where(VideoMetric.video_id == video_id))
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matching video for returned OAuth state.",
        )

    flow = build_oauth_flow(state=state)
    try:
        await run_in_threadpool(flow.fetch_token, code=code)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not exchange authorization code for tokens.",
        ) from exc

    credentials = flow.credentials
    if not credentials.refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google did not return a refresh token. Re-consent may be required.",
        )

    video.refresh_token = token_cipher.encrypt(credentials.refresh_token)
    video.is_authorized = True
    await db.commit()

    if redirect_url:
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)

    return OAuthCallbackResponse(
        message="YouTube authorization was completed successfully.",
        video_id=video.video_id,
        is_authorized=video.is_authorized,
    )
