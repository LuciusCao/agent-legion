"""Request models for the local Worker control service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WorkerConfigPayload(BaseModel):
    """Partial update：仅提交的字段会被更新，未提供的字段保持现状。

    extra="forbid"：退役字段（如旧版 opt-in 的 `runtimes`）与笔误字段一律
    422 fail-fast，不留静默忽略（issue #254）。"""

    model_config = ConfigDict(extra="forbid")

    claim_enabled: bool | None = None
    capabilities: list[str] | None = None
    host_url: str | None = None
    worker_id: str | None = None
    name: str | None = None
    # 反选停用（issue #254）：生效 runtimes = 本机探测到的已安装集合 − 本字段。
    disabled_runtimes: list[str] | None = None
    max_concurrency: int | None = None
    max_code_concurrency: int | None = None
    upload_max_concurrency: int | None = None
    models: list[dict[str, str]] | None = None
    labels: dict[str, str] | None = None
    poll_interval_seconds: float | None = None
    heartbeat_interval_seconds: float | None = None
    shutdown_grace_seconds: float | None = None
    # 兼容通道：等价于向 /api/register-tokens 添加一个 token（老客户端/脚本）。
    register_token: str | None = None


class RegisterTokenPayload(BaseModel):
    """添加一个 Host 签发的 scoped 注册 token（明文仅写入，永不回显）。"""

    register_token: str
