"""Claim deadlock retry (issue #437): SQLSTATE 40P01 retried once, then surfaced.

The claim transaction can lose a lock race on the status-counter rows and
come back as psycopg DeadlockDetected (40P01).
``AgentExecutionBroker.claim`` runs the transaction through
``claim_retry.claim_with_retry``, which retries the whole transaction once —
write_transaction rolled the failed connection back and closed it, so the
retry starts from a clean connection — and lets a second failure propagate
to the route's 500. These tests inject the exception at the
``claim_in_transaction`` boundary (the call site claim_retry.py imported):

1. one deadlock then success -> the claim succeeds, no 500;
2. deadlock on every attempt -> exactly one retry, then the error surfaces;
3. a non-deadlock database error -> no retry, surfaced immediately;
4. the retry decision reads ``sqlstate``, not the exception type.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from psycopg import Error
from psycopg.errors import DeadlockDetected, OperationalError

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker import claim as claim_module
from server.app.agent_control.registry import AgentWorkerRegistry
from tests.helpers.agent_worker_api import broker as _broker
from tests.helpers.agent_worker_api import seed_request
from tests.postgres_support import TEST_DATABASE_URL

_WORKER_ID = "dlr-worker"


class _ScriptedError(Error):
    """A psycopg Error carrying a sqlstate without a server round trip."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"scripted {sqlstate}")
        self.sqlstate = sqlstate


def _make_broker(data_dir: Path, **kwargs) -> AgentExecutionBroker:
    return _broker(data_dir, **kwargs)


def _patch_claim(monkeypatch: pytest.MonkeyPatch, replacement) -> None:
    """Patch claim_in_transaction at the call site: claim_retry.py imported
    the symbol directly (broker.claim goes through claim_with_retry)."""
    import server.app.agent_broker.claim_retry as claim_retry_module

    monkeypatch.setattr(claim_retry_module, "claim_in_transaction", replacement)


def _seed(job_db, job_id: str) -> None:
    seed_request(job_db, job_id=job_id)
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=_WORKER_ID,
        name=_WORKER_ID,
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )


def test_deadlock_once_then_success(job_db, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(job_db, job_id="dlr-job-1")
    executor = _make_broker(job_db.jobs_dir.parent)
    calls = {"n": 0}
    real_claim = claim_module.claim_in_transaction

    def claim_once_then_fail(broker, conn, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise DeadlockDetected("deadlock detected")
        return real_claim(broker, conn, *args, **kwargs)

    _patch_claim(monkeypatch, claim_once_then_fail)

    claimed = executor.claim(_WORKER_ID)

    assert calls["n"] == 2, "the 40P01 must trigger exactly one retry"
    assert claimed is not None
    assert claimed.job_id == "dlr-job-1"


def test_deadlock_every_attempt_surfaces_after_one_retry(
    job_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(job_db, job_id="dlr-job-2")
    executor = _make_broker(job_db.jobs_dir.parent)
    calls = {"n": 0}

    def always_deadlock(broker, conn, *args, **kwargs):
        calls["n"] += 1
        raise DeadlockDetected("deadlock detected")

    _patch_claim(monkeypatch, always_deadlock)

    with pytest.raises(DeadlockDetected):
        executor.claim(_WORKER_ID)

    assert calls["n"] == 2, "exactly one retry, then the 500 stands"


def test_non_deadlock_error_is_not_retried(job_db, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(job_db, job_id="dlr-job-3")
    executor = _make_broker(job_db.jobs_dir.parent)
    calls = {"n": 0}

    def always_operational(broker, conn, *args, **kwargs):
        calls["n"] += 1
        raise OperationalError("connection reset")

    _patch_claim(monkeypatch, always_operational)

    with pytest.raises(OperationalError):
        executor.claim(_WORKER_ID)

    assert calls["n"] == 1, "only 40P01 is retried"


def test_sqlstate_matched_not_type(job_db, monkeypatch: pytest.MonkeyPatch) -> None:
    # The retry decision reads ``sqlstate``; an Error subclass that is not
    # DeadlockDetected still retries when it carries 40P01 — the SQLSTATE is
    # the contract, not the exception type.
    _seed(job_db, job_id="dlr-job-4")
    executor = _make_broker(job_db.jobs_dir.parent)
    calls = {"n": 0}
    real_claim = claim_module.claim_in_transaction

    def sqlstate_40p01_first(broker, conn, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _ScriptedError("40P01")
        return real_claim(broker, conn, *args, **kwargs)

    _patch_claim(monkeypatch, sqlstate_40p01_first)

    claimed = executor.claim(_WORKER_ID)

    assert calls["n"] == 2
    assert claimed is not None
    assert claimed.job_id == "dlr-job-4"


def test_successful_claim_retries_nothing(job_db, monkeypatch: pytest.MonkeyPatch) -> None:
    # The happy path must not pay for the retry loop: one call, no exception.
    _seed(job_db, job_id="dlr-job-5")
    executor = _make_broker(job_db.jobs_dir.parent)
    calls = {"n": 0}
    real_claim = claim_module.claim_in_transaction

    def counting(broker, conn, *args, **kwargs):
        calls["n"] += 1
        return real_claim(broker, conn, *args, **kwargs)

    _patch_claim(monkeypatch, counting)

    claimed = executor.claim(_WORKER_ID)

    assert calls["n"] == 1
    assert claimed is not None
