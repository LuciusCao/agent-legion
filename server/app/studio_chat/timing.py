"""Immutable per-handle Studio timing settings (#1153 A2)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_PROMPT_TIMEOUT_SECONDS = 3600
DEFAULT_CANCEL_GRACE_SECONDS = 30
DEFAULT_UNPROMPTED_IDLE_TIMEOUT_SECONDS = 900
DEFAULT_CLOSE_GRACE_SECONDS = 5


def _coerce_seconds(value: Any) -> float | int:
    number = float(value)
    return int(number) if number.is_integer() else number


@dataclass(frozen=True)
class StudioTimingConfig:
    prompt_timeout_seconds: float = DEFAULT_PROMPT_TIMEOUT_SECONDS
    cancel_grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS
    unprompted_idle_timeout_seconds: float = DEFAULT_UNPROMPTED_IDLE_TIMEOUT_SECONDS
    close_grace_seconds: float = DEFAULT_CLOSE_GRACE_SECONDS

    def __post_init__(self) -> None:
        for name in (
            "prompt_timeout_seconds",
            "cancel_grace_seconds",
            "unprompted_idle_timeout_seconds",
            "close_grace_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"studio_chat.{name} must be a finite positive number")

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> StudioTimingConfig:
        values = config.get("studio_chat", {})
        if not isinstance(values, dict):
            raise ValueError("studio_chat must be a mapping")
        return cls(
            **{
                key: _coerce_seconds(values[key])
                for key in cls.__dataclass_fields__
                if key in values
            }
        )


def reject_yaml_studio_timing(config: dict[str, Any], environ: Mapping[str, str]) -> None:
    values = config.get("studio_chat")
    if not values:
        return
    if not isinstance(values, dict):
        raise ValueError("studio_chat must be a mapping")
    overridden = {
        name
        for name in StudioTimingConfig.__dataclass_fields__
        if environ.get(f"AGENT_LEGION_STUDIO_CHAT_{name.upper()}") is not None
    }
    if unsupported := set(values) - overridden:
        name = sorted(unsupported)[0]
        raise ValueError(
            f"studio_chat.{name} is env-only; use AGENT_LEGION_STUDIO_CHAT_{name.upper()}"
        )


def prompt_timeout_for(runtime: Any, default: float = DEFAULT_PROMPT_TIMEOUT_SECONDS) -> float:
    value = getattr(getattr(runtime, "handle", None), "prompt_timeout_seconds", default)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def format_turn_timeout(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:g} 小时"
    if seconds >= 60:
        return f"{seconds / 60:g} 分钟"
    return f"{seconds:g} 秒"


def turn_timeout_detail(runtime: Any, default: float) -> str:
    return f"运行超过 {format_turn_timeout(prompt_timeout_for(runtime, default))}已被终止"
