from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from server.app.db import Database
from server.app.pipeline.openclaw import OpenClawRunner
from server.app.settings import Settings


@dataclass
class PhaseContext:
    video: dict[str, Any]
    video_dir: Path
    settings: Settings
    db: Database
    log_path: Path
    providers: list[Any] | None
    openclaw_runner: OpenClawRunner | None
    run: dict[str, Any]


class PhaseHandler(Protocol):
    def __call__(self, ctx: PhaseContext) -> None: ...


class PhaseExecutorRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, PhaseHandler] = {}

    def register(self, phase: str, handler: PhaseHandler) -> None:
        self._handlers[phase] = handler

    def execute(self, phase: str, ctx: PhaseContext) -> None:
        handler = self._handlers.get(phase)
        if handler is None:
            raise ValueError(f"Unknown phase: {phase}")
        handler(ctx)

    def has(self, phase: str) -> bool:
        return phase in self._handlers

    def handler_names(self) -> set[str]:
        return set(self._handlers.keys())
