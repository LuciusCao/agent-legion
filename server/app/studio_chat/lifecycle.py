"""Service-wide startup admission and shutdown draining (STUDIO-RUNTIME-001).

The condition serializes admission with sealing, not the startup work itself.
Shutdown drains admitted create/resume operations before snapshotting runtimes;
therefore no producer can register a successor after that snapshot. Neither
the condition nor a registry/runtime lock is held while waiting for ACP ready,
running callbacks, or joining subprocess threads. Concurrent shutdown callers
serialize through a separate lock and cannot return before cleanup completes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import TYPE_CHECKING, Concatenate, ParamSpec, TypeVar

from server.app.services.job_errors import ConflictError

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService

P = ParamSpec("P")
R = TypeVar("R")


class ServiceLifecycle:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._shutdown_lock = threading.Lock()
        self._sealed = False
        self._starting = 0

    @contextmanager
    def starting(self) -> Iterator[None]:
        with self._condition:
            if self._sealed:
                raise ConflictError("Studio chat service is shutting down")
            self._starting += 1
        try:
            yield
        finally:
            with self._condition:
                self._starting -= 1
                self._condition.notify_all()

    @contextmanager
    def shutdown(self) -> Iterator[None]:
        with self._shutdown_lock:
            with self._condition:
                self._sealed = True
                self._condition.wait_for(lambda: self._starting == 0)
            yield


def starting_operation(
    operation: Callable[Concatenate[StudioChatService, P], R],
) -> Callable[Concatenate[StudioChatService, P], R]:
    @wraps(operation)
    def admitted(service: StudioChatService, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with service._lifecycle.starting():
            return operation(service, *args, **kwargs)

    return admitted
