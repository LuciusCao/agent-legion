"""Prompt-turn terminal types and orphaned-task result handling."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class PromptWedgedError(RuntimeError):
    """The turn outlived the timeout and post-cancel grace: transport is wedged."""


@dataclass(frozen=True)
class PromptTurnResult:
    """Distinguish a timeout-terminated response from an ordinary turn end."""

    response: Any
    timed_out: bool


def _log_orphan_result(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.warning("studio chat ACP orphaned prompt task failed: %s", exc)


def _orphan(task: asyncio.Task[Any]) -> None:
    """Cancel locally and consume the eventual result during teardown."""
    task.cancel()
    task.add_done_callback(_log_orphan_result)
