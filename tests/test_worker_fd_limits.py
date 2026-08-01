"""Tests for ``worker.fd_limits.raise_fd_limit``."""

from __future__ import annotations

import resource

import pytest

from worker.fd_limits import MIN_NOFILE, raise_fd_limit


def test_raise_fd_limit_is_idempotent_with_current_limits() -> None:
    soft, hard = raise_fd_limit()
    assert soft <= hard or hard == resource.RLIM_INFINITY
    again_soft, again_hard = raise_fd_limit()
    assert (again_soft, again_hard) == (soft, hard)


def test_raise_fd_limit_lifts_low_soft_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(resource, "getrlimit", lambda _r: (256, resource.RLIM_INFINITY))
    monkeypatch.setattr(resource, "setrlimit", lambda _r, limits: calls.append(limits))
    soft, hard = raise_fd_limit()
    assert (soft, hard) == (MIN_NOFILE, resource.RLIM_INFINITY)
    assert calls == [(MIN_NOFILE, resource.RLIM_INFINITY)]


def test_raise_fd_limit_clamps_to_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(resource, "getrlimit", lambda _r: (256, 4096))
    monkeypatch.setattr(resource, "setrlimit", lambda _r, limits: calls.append(limits))
    soft, hard = raise_fd_limit()
    assert (soft, hard) == (4096, 4096)
    assert calls == [(4096, 4096)]


def test_raise_fd_limit_never_lowers(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_setrlimit(_r: int, _limits: tuple[int, int]) -> None:
        raise AssertionError("setrlimit must not be called")

    monkeypatch.setattr(resource, "getrlimit", lambda _r: (65536, resource.RLIM_INFINITY))
    monkeypatch.setattr(resource, "setrlimit", _fail_setrlimit)
    soft, hard = raise_fd_limit()
    assert (soft, hard) == (65536, resource.RLIM_INFINITY)
