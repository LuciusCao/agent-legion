"""Per-kind claim windows (issue #125): a code flood never starves agent claims.

Regression tests for the 2026-08-18 prod incident: with code concurrency
open on Workers, ~14k queued code requests crowded the single cross-kind
FIFO window and claim passes never reached the agent requests queued behind
them, driving agent completions to 0/min. Each kind now scans its own
window ladder with its own attempt budget (``claim_windows``).
"""

from __future__ import annotations

from server.app.agent_broker import AgentExecutionBroker, claim_windows
from server.app.agent_control.registry import AgentWorkerRegistry
from tests.helpers.agent_worker_api import (
    enqueue_code as _enqueue_code,
)
from tests.helpers.agent_worker_api import (
    insert_code_job_rows as _insert_code_job_rows,
)
from tests.helpers.agent_worker_api import seed_request as _seed_request
from tests.postgres_support import TEST_DATABASE_URL

# Above the deepest SCAN_ROUNDS per-workspace cap (512): a cross-kind FIFO
# window can never page through this flood to reach the agent request.
_CODE_FLOOD = 520


def _broker(data_dir) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=data_dir)


def _register_dual_worker(*, max_concurrency: int = 2, max_code_concurrency: int = 1) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-dual",
        name="worker",
        runtimes=["pi"],
        capabilities=["package", "generate"],
        max_concurrency=max_concurrency,
        max_code_concurrency=max_code_concurrency,
        labels={"arch": "arm64"},
        protocol_version=2,
    )


def test_code_flood_never_starves_agent_claim(job_db) -> None:
    """A saturated code pool + deep code queue must not block agent claims."""
    broker = _broker(job_db.jobs_dir.parent)
    for index in range(_CODE_FLOOD):
        _insert_code_job_rows(job_db, job_id=f"job-code-{index}")
        _enqueue_code(broker, job_id=f"job-code-{index}")
    _register_dual_worker()
    # Fill the only code slot while no agent request exists: every later
    # code candidate is a code_capacity_full skip — the incident shape.
    first = broker.claim("worker-dual")
    assert first is not None and first.kind == "code"
    _seed_request(job_db, job_id="job-agent", limit=20)

    claimed = broker.claim("worker-dual")

    assert claimed is not None
    assert claimed.kind == "agent"
    assert claimed.job_id == "job-agent"


def test_unclaimable_code_does_not_consume_agent_attempt_budget(job_db, monkeypatch) -> None:
    """The attempt budget is per kind: code candidates failing past the
    compatibility filters (here: paused jobs) burn only code attempts."""
    monkeypatch.setattr(claim_windows, "MAX_CLAIM_ATTEMPTS", 2)
    broker = _broker(job_db.jobs_dir.parent)
    for index in range(3):
        _insert_code_job_rows(job_db, job_id=f"job-paused-{index}")
        _enqueue_code(broker, job_id=f"job-paused-{index}")
    with job_db.connect() as conn:
        conn.execute("update jobs set execution_paused=1 where id like 'job-paused-%'")
    _seed_request(job_db, job_id="job-agent", limit=20)
    _register_dual_worker(max_concurrency=1, max_code_concurrency=1)

    claimed = broker.claim("worker-dual")

    assert claimed is not None
    assert claimed.kind == "agent"
