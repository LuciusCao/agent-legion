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
    """Object-store summary; credentials reduce to a derivation kind.

    ``backend`` is a display-only label inferred from the endpoint host
    (e.g. SeaweedFS / RustFS / MinIO / AWS S3); the platform itself only
    ever speaks the S3 API, so it never knows the server product for
    certain — an unrecognized host falls back to "S3 兼容（<host>）".
    """

    configured: bool
    backend: str
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
