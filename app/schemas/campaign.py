import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.video_metric import DashboardVideoItem


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class CampaignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    video_quantity: int = 0
    created_at: datetime


class CampaignDashboardResponse(BaseModel):
    campaign_id: uuid.UUID
    title: str
    videos: list[DashboardVideoItem]
