"""Request models for the local Worker control service."""

from __future__ import annotations

from pydantic import BaseModel


class WorkerConfigPayload(BaseModel):
    """Partial update：仅提交的字段会被更新，未提供的字段保持现状。"""

    claim_enabled: bool | None = None
    capabilities: list[str] | None = None
    host_url: str | None = None
    worker_id: str | None = None
    name: str | None = None
    runtimes: list[str] | None = None
    max_concurrency: int | None = None
    upload_max_concurrency: int | None = None
    models: list[dict[str, str]] | None = None
    labels: dict[str, str] | None = None
    poll_interval_seconds: float | None = None
    heartbeat_interval_seconds: float | None = None
    shutdown_grace_seconds: float | None = None
    register_token: str | None = None
