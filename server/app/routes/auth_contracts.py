from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class BootstrapRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    display_name: str = ""


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    role: Literal["admin", "member"]
    disabled_at: datetime | None
    created_at: datetime


class LoginResponse(BaseModel):
    user: UserResponse


class MeResponse(BaseModel):
    user: UserResponse


class BootstrapStatusResponse(BaseModel):
    available: bool


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    display_name: str = ""
    role: Literal["admin", "member"] = "member"


class UserPatchRequest(BaseModel):
    display_name: str | None = None
    role: Literal["admin", "member"] | None = None
    password: str | None = Field(default=None, min_length=1)
    disabled: bool | None = None


class UsersResponse(BaseModel):
    users: list[UserResponse]


class MemberResponse(BaseModel):
    id: str
    username: str
    display_name: str
    user_role: Literal["admin", "member"]
    disabled_at: datetime | None
    member_role: Literal["editor", "viewer"]


class MembersResponse(BaseModel):
    members: list[MemberResponse]


class MemberPutRequest(BaseModel):
    user_id: str = Field(min_length=1)
    role: Literal["editor", "viewer"]
