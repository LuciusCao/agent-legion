"""Test-only migration phase hooks.

This module exists so that failure-injection drills can interrupt migrations at
internal phase boundaries without adding production behavior flags.  Production
code never sets a hook.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token

_PHASE_HOOK: ContextVar[Callable[[str], None] | None] = ContextVar("_phase_hook", default=None)


def _call_phase_hook(phase: str) -> None:
    """Invoke the currently registered phase hook, if any."""
    hook = _PHASE_HOOK.get()
    if hook is not None:
        hook(phase)


def _set_phase_hook(
    hook: Callable[[str], None] | None,
) -> Token[Callable[[str], None] | None]:
    """Install *hook* for the current context and return the reset token."""
    return _PHASE_HOOK.set(hook)


def _reset_phase_hook(token: Token[Callable[[str], None] | None]) -> None:
    """Reset the phase hook using a previously returned token."""
    _PHASE_HOOK.reset(token)
