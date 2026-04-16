import uuid
from enum import Enum
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class UserType(str, Enum):
    content_creator = "content_creator"
    business_owner = "business_owner"
    visitor = "visitor"

class UserBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    business_name: str | None = None
    user_type: UserType

class UserCreate(UserBase):
    password: str = Field(..., min_length=6)

class UserRead(UserBase):
    id: uuid.UUID

class UserLogin(BaseModel):
    email: EmailStr
    password: str
