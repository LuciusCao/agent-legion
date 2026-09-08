"""Result-commit peak-shaving gate (issue #521).

A completion wave's commits are GIL-bound threadpool work; unbounded they
saturate the single-process control plane and starve claim/heartbeat. The
route bounds concurrent ``commit_agent_result`` offloads with an
``asyncio.Semaphore`` (``agent_workers.max_concurrent_result_commits``,
0 = disabled). These tests pin the gate's observable behavior through the
HTTP surface: peak concurrency stays at the configured bound, and 0 keeps
the offload un-gated.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from tests.helpers.agent_worker_api import (
    claim as _claim,
)
from tests.helpers.agent_worker_api import (
    empty_archive as _empty_archive,
)
from tests.helpers.agent_worker_api import (
    make_app as _make_app,
)
from tests.helpers.agent_worker_api import (
    register as _register,
)
from tests.helpers.agent_worker_api import (
    seed_request as _seed_request,
)

_HEADERS_META = json.dumps({"status": "failed", "exit_code": 1})


def _report(client: TestClient, token: str, claimed: dict) -> None:
    response = client.post(
        f"/api/agent-executions/{claimed['execution_id']}/result",
        headers={
            "X-Agent-Worker-Token": token,
            "X-Agent-Lease-Id": claimed["lease_id"],
            "X-Agent-Result": _HEADERS_META,
        },
        content=_empty_archive(),
    )
    assert response.status_code == 204, response.text


def _instrument_commit(monkeypatch, tracker: dict) -> None:
    """Replace the commit with a slow no-op that tracks peak concurrency."""

    from server.app.routes import agent_workers as route_module

    lock = threading.Lock()
    original = route_module.commit_agent_result

    def _slow_commit(*args, **kwargs) -> None:
        with lock:
            tracker["active"] = tracker.get("active", 0) + 1
            tracker["peak"] = max(tracker.get("peak", 0), tracker["active"])
        time.sleep(0.05)
        with lock:
            tracker["active"] -= 1
        # The real commit owns the staging-file rename; a no-op leaves the
        # staged body for the route's finally to reclaim, which is the
        # already-tested failure-path behavior.
        return None

    _ = original
    monkeypatch.setattr(route_module, "commit_agent_result", _slow_commit)


def _gated_app(tmp_path: Path, monkeypatch, gate: int):
    """Build an app whose router factory saw max_concurrent_result_commits=gate.

    The gate semaphore is constructed when ``create_agent_workers_router``
    runs (inside create_app), so the setting must be in place before app
    construction — patching ``app.state`` after the fact is too late (the
    sibling max_archive_bytes knob is read per-request, this one at wiring).
    """
    import server.app.main as main_module
    from server.app.settings import Settings

    original_load = main_module.load_settings

    def _load_with_gate(*args, **kwargs) -> Settings:
        settings = original_load(*args, **kwargs)
        runtime = settings.executor_runtime.model_dump()
        runtime["agent_workers"]["max_concurrent_result_commits"] = gate
        settings.executor_runtime = type(settings.executor_runtime).model_validate(runtime)
        return settings

    monkeypatch.setattr(main_module, "load_settings", _load_with_gate)
    return _make_app(tmp_path)


def _run_four_parallel_reports(app, client, token) -> list[dict]:
    """Seed four jobs, claim four executions, report all four in parallel."""
    for index in range(4):
        _seed_request(app.state.job_db, job_id=f"gate-job-{index}", limit=20)
    claims = [_claim(client, token) for _ in range(4)]
    threads = []
    for claimed in claims:
        thread = threading.Thread(target=_report, args=(client, token, claimed))
        threads.append(thread)
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    return claims


def test_gate_bounds_peak_concurrent_commits(tmp_path: Path, monkeypatch) -> None:
    """Four simultaneous reports through a gate of 2 must never run more
    than 2 commits at once — the queued ones park as coroutines."""
    app = _gated_app(tmp_path, monkeypatch, gate=2)
    tracker: dict[str, int] = {}
    _instrument_commit(monkeypatch, tracker)

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        # One enqueued request per job (seed_request enqueues exactly one;
        # the one-active-request constraint dedups per job+node), so four
        # parallel reports need four jobs.
        _run_four_parallel_reports(app, client, token)

    assert tracker["peak"] <= 2, tracker
    assert tracker.get("peak", 0) >= 1


def test_gate_disabled_runs_all_concurrently(tmp_path: Path, monkeypatch) -> None:
    """max_concurrent_result_commits=0 is the kill-switch: the un-gated
    offload must let four parallel reports run concurrently (subagent review
    on #530: a single-report peak==1 assert is vacuous — a regression
    turning 0 into Semaphore(1) would still pass it)."""
    app = _gated_app(tmp_path, monkeypatch, gate=0)
    tracker: dict[str, int] = {}
    _instrument_commit(monkeypatch, tracker)

    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        _run_four_parallel_reports(app, client, token)

    # Un-gated: the four slow no-op commits overlap freely.
    assert tracker["peak"] >= 2, tracker

    # The configuration contract: 0 is a valid value (the kill-switch).
    from server.app.configuration.executor_runtime import AgentWorkersRuntimeConfig

    config = AgentWorkersRuntimeConfig(max_concurrent_result_commits=0)
    assert config.max_concurrent_result_commits == 0


def test_gate_slot_released_when_commit_raises(tmp_path: Path, monkeypatch) -> None:
    """A commit exception must release the gate slot (subagent review on
    #530): after a raising report, subsequent reports still commit through
    the same gate instead of deadlocking on a leaked slot."""
    from fastapi import HTTPException

    app = _gated_app(tmp_path, monkeypatch, gate=1)
    tracker: dict[str, int] = {}
    _instrument_commit(monkeypatch, tracker)

    from server.app.routes import agent_workers as route_module

    def _raising_commit(*args, **kwargs) -> None:
        tracker["raised"] = tracker.get("raised", 0) + 1
        raise HTTPException(status_code=500, detail="commit exploded")

    # First report: the commit raises inside the gate.
    monkeypatch.setattr(route_module, "commit_agent_result", _raising_commit)
    with TestClient(app) as client:
        token = _register(client)["worker_token"]
        _seed_request(app.state.job_db, job_id="gate-job-fail", limit=20)
        claimed = _claim(client, token)
        failed = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={
                "X-Agent-Worker-Token": token,
                "X-Agent-Lease-Id": claimed["lease_id"],
                "X-Agent-Result": _HEADERS_META,
            },
            content=_empty_archive(),
        )
        assert failed.status_code == 500

        # Restore the healthy commit and report the SAME (still-claimed)
        # execution again through the same gate=1 — a leaked slot would
        # hang this request until the client timeout.
        monkeypatch.setattr(route_module, "commit_agent_result", _slow_commit_factory(tracker)[0])
        _report(client, token, claimed)

    assert tracker["raised"] == 1
    assert tracker.get("peak", 0) >= 1


def _slow_commit_factory(tracker: dict):
    from server.app.routes import agent_workers as route_module

    lock = threading.Lock()

    def _slow_commit(*args, **kwargs) -> None:
        with lock:
            tracker["active"] = tracker.get("active", 0) + 1
            tracker["peak"] = max(tracker.get("peak", 0), tracker["active"])
        time.sleep(0.05)
        with lock:
            tracker["active"] -= 1

    _ = route_module
    return _slow_commit, lock
