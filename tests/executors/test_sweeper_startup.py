"""Startup-sweep split (#139): lease hygiene stays synchronous, bundle GC defers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from server.app.executors import sweeper as sweeper_module
from server.app.executors.sweeper import SweeperThread


def _make_sweeper(**kwargs) -> tuple[SweeperThread, MagicMock, MagicMock]:
    leases = MagicMock()
    broker = MagicMock()
    broker.fail_stale_definition_requests.return_value = []
    return SweeperThread(leases, broker, **kwargs), leases, broker


@pytest.mark.no_db
def test_startup_sweep_skips_terminal_bundle_reap() -> None:
    """#139: the synchronous startup sweep must not reap terminal bundles —
    its first pass scans all of terminal history and would block readiness.
    The lease/claim hygiene that must precede serving work still runs."""
    sweeper, leases, broker = _make_sweeper(interval_seconds=3600.0)

    with patch.object(sweeper_module, "fail_unclaimable_model_requests", return_value=[]):
        sweeper.start()
        sweeper.stop()

    broker.reap_terminal_bundles.assert_not_called()
    broker.sweep_expired_claims.assert_called_once()
    leases.expire_stale.assert_called_once()
    leases.recover_orphaned_running_jobs.assert_called_once()


@pytest.mark.no_db
def test_periodic_sweep_reaps_terminal_bundles() -> None:
    """The background loop keeps reaping: the GC is deferred, not dropped."""
    sweeper, _, broker = _make_sweeper()

    with patch.object(sweeper_module, "fail_unclaimable_model_requests", return_value=[]):
        sweeper._sweep_once()

    broker.reap_terminal_bundles.assert_called_once()


@pytest.mark.no_db
def test_periodic_sweep_runs_skill_execution_gc() -> None:
    """The optional skill sweeper runs in the periodic pass: leaked
    execution snapshots in the runs dir are GC'd alongside bundles."""
    swept = []

    def _skill_sweeper() -> int:
        swept.append(1)
        return 2

    sweeper, _, broker = _make_sweeper(skill_sweeper=_skill_sweeper)

    with patch.object(sweeper_module, "fail_unclaimable_model_requests", return_value=[]):
        sweeper._sweep_once()

    broker.reap_terminal_bundles.assert_called_once()
    assert len(swept) == 1


@pytest.mark.no_db
def test_skill_execution_gc_failure_does_not_break_sweeper() -> None:
    """A GC exception is logged and swallowed — one scratch-dir failure must
    not stop lease expiry or the rest of the sweep."""

    def _boom() -> int:
        raise RuntimeError("scratch dir vanished")

    sweeper, leases, _ = _make_sweeper(skill_sweeper=_boom)

    with patch.object(sweeper_module, "fail_unclaimable_model_requests", return_value=[]):
        sweeper._sweep_once()  # must not raise

    leases.expire_stale.assert_called_once()
