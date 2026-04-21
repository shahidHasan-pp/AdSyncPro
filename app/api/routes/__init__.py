from fastapi import APIRouter


from app.api.routes.auth import router as auth_router
from app.api.routes.campaigns import router as campaign_router
from app.api.routes.user import router as user_router
from app.api.routes.videos import router as videos_router

api_router = APIRouter()
api_router.include_router(campaign_router)
api_router.include_router(auth_router)
api_router.include_router(user_router)
api_router.include_router(videos_router)
