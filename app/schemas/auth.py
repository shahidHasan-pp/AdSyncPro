from pydantic import BaseModel
from app.schemas.user import UserRead


class Token(BaseModel):
    access_token: str
    token_type: str


class UserLoginResponse(BaseModel):
    user: UserRead
    token: Token


class OAuthLoginResponse(BaseModel):
    auth_url: str
    state: str


class OAuthCallbackResponse(BaseModel):
    message: str
    video_id: str
    is_authorized: bool

