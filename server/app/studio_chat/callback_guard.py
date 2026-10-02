"""Serialize nonblocking ACP callbacks with retirement of their own generation."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.studio_chat.callbacks import ServiceCallbacks


def owned_callback(method: Callable[..., None]) -> Callable[..., None]:
    """Do not use for permission waits or thread joins: those release the lock."""

    @wraps(method)
    def guarded(callbacks: ServiceCallbacks, *args: Any, **kwargs: Any) -> None:
        runtime = callbacks.runtime
        if runtime is None:
            return
        with runtime.lock:
            if runtime.closed or callbacks._service.runtime(callbacks._session_id) is not runtime:
                return
            method(callbacks, *args, **kwargs)

    return guarded
