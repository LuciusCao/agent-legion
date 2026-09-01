from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DatabaseConnectionView(BaseModel):
    """Display-safe database summary: password masked, query string dropped."""

    engine: str
    host: str
    port: int | None
    name: str
    user: str
    password_set: bool
    masked_url: str


class StorageConnectionView(BaseModel):
    """Object-store summary; credentials reduce to a derivation kind."""

    configured: bool
    endpoint_url: str
    public_endpoint_url: str
    bucket: str
    region: str
    credentials: Literal["static", "default-chain", "unconfigured"]
    reachable: bool


class InfraConnectionsResponse(BaseModel):
    database: DatabaseConnectionView
    storage: StorageConnectionView


class InfraConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["database", "storage"]


class InfraConnectionTestResponse(BaseModel):
    """Connectivity probe verdict; ``reason`` is ``TypeName: message`` on failure."""

    target: Literal["database", "storage"]
    ok: bool
    reason: str | None = None
