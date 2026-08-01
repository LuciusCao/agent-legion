from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.app.executors.models import ExecutionStatus
from server.app.executors.runtime_config import PiRuntimeConfig


@dataclass(frozen=True)
class PiConfig:
    binary: str = "pi"
    flavor: str = "pi"  # "pi" | "velites"；合法性由 from_config / PiRuntimeConfig 保证
    provider: str = ""
    model: str = ""
    thinking: str = "low"
    timeout_seconds: int = 600
    cancellation_grace_seconds: int = 5
    environment: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_runtime(cls, config: PiRuntimeConfig) -> PiConfig:
        """Build an immutable PiConfig from a validated PiRuntimeConfig."""
        return cls(
            binary=config.binary,
            flavor=config.flavor,
            provider=config.provider,
            model=config.model,
            thinking=config.thinking or "low",
            timeout_seconds=config.timeout_seconds,
            cancellation_grace_seconds=config.cancellation_grace_seconds,
            environment=dict(config.environment),
        )

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> PiConfig:
        """Build a PiConfig from a raw configuration dictionary."""
        binary = raw.get("binary")
        if not binary or not isinstance(binary, str):
            raise ValueError("Pi binary is required")
        # 未知 flavor fail-fast（与 PiRuntimeConfig 的 Literal 校验同一惯例）。
        flavor = str(raw.get("flavor", "pi")).strip() or "pi"
        if flavor not in ("pi", "velites"):
            raise ValueError(f"Pi flavor must be 'pi' or 'velites', got {flavor!r}")
        if flavor == "velites" and binary == "pi":
            binary = "velites"  # 与 PiRuntimeConfig 同一归一化：binary 默认值跟随 flavor
        timeout = raw.get("timeout_seconds", 600)
        if not isinstance(timeout, int) or timeout < 1:
            raise ValueError("Pi timeout_seconds must be a positive integer")
        env = raw.get("environment", {})
        if not isinstance(env, dict):
            env = {}
        return cls(
            binary=binary,
            flavor=flavor,
            provider=str(raw.get("provider", "")),
            model=str(raw.get("model", "")),
            thinking=str(raw.get("thinking", "low")),
            timeout_seconds=timeout,
            environment={str(k): str(v) for k, v in env.items()},
        )


@dataclass(frozen=True)
class PiRunResult:
    status: ExecutionStatus
    exit_code: int
    command: list[str]
    run_dir: Path
    session_dir: Path
    error_message: str = ""
    skill_version: str = ""
