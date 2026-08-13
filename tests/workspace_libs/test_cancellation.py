"""workspace_libs cancellation primitives and the Host-side re-export shim."""

from __future__ import annotations

import multiprocessing
import threading
import time

import pytest

from workspace_libs.cancellation import CancellationToken, CancelledError

pytestmark = pytest.mark.no_db


def test_token_defaults_to_threading_event() -> None:
    token = CancellationToken()
    assert not token.is_cancelled()
    token.cancel()
    assert token.is_cancelled()


def test_token_wraps_supplied_event() -> None:
    event = multiprocessing.Event()
    token = CancellationToken(event)
    assert not token.is_cancelled()
    event.set()
    assert token.is_cancelled()


def test_token_wait_blocks_until_cancelled() -> None:
    token = CancellationToken()
    threading.Timer(0.05, token.cancel).start()
    assert token.wait(timeout=2)
    assert not CancellationToken().wait(timeout=0.01)


def test_raise_if_cancelled() -> None:
    token = CancellationToken()
    token.raise_if_cancelled()
    token.cancel()
    with pytest.raises(CancelledError, match="execution was cancelled"):
        token.raise_if_cancelled()


def test_server_module_reexports_same_objects() -> None:
    """server.app.executors.cancellation is a shim over workspace_libs."""
    import server.app.executors.cancellation as host_module

    assert host_module.CancellationToken is CancellationToken
    assert host_module.CancelledError is CancelledError


def test_check_cancellation_shim() -> None:
    """The Host-side helper still raises via the moved token."""
    from server.app.executors.cancellation import check_cancellation

    token = CancellationToken()
    check_cancellation({"cancellation": token})
    token.cancel()
    with pytest.raises(CancelledError):
        check_cancellation({"cancellation": token})
    check_cancellation(None)  # no token, no raise
    check_cancellation({"cancellation": object()})  # foreign object ignored


def test_wait_timeout_returns_false_promptly() -> None:
    token = CancellationToken()
    start = time.monotonic()
    assert not token.wait(timeout=0.05)
    assert time.monotonic() - start < 1
