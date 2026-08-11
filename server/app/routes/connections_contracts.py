from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConnectionCreate(BaseModel):
    key: str = Field(min_length=1)
    type: str = Field(min_length=1)
    display_name: str = ""
    config: dict[str, Any] = {}


class ConnectionUpdate(BaseModel):
    display_name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class ConnectionTokenStatus(BaseModel):
    expires_at: str | None = None
    refreshed_at: str | None = None


class ConnectionView(BaseModel):
    key: str
    type: str
    display_name: str
    config: dict[str, Any]
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None
    token: ConnectionTokenStatus | None = None


class ConnectionListResponse(BaseModel):
    connections: list[ConnectionView]


class ConnectionTypeView(BaseModel):
    type: str
    description: str
    required_config_keys: list[str]
    secret_keys: list[str]


class ConnectionTypesResponse(BaseModel):
    types: list[ConnectionTypeView]


class ConnectionTestResponse(BaseModel):
    ok: bool
    message: str
