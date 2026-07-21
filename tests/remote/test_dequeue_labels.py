"""Worker labels + capability ``requires_labels`` dequeue routing (phase 4, task 5).

Affinity scheduling: a remote capability may declare ``requires_labels``
(e.g. ``{"mem_gb": ">=16"}``); ``dequeue`` then only hands queued rows of that
capability to workers whose self-reported labels satisfy every constraint.
Constraint values are either ``">=<int>"`` (numeric comparison) or a non-empty
literal (exact string match); an unknown label never satisfies a constraint.
The filter runs in memory inside the claim transaction — FIFO ordering and the
slots cap are unchanged, constraints only stack on top of them.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.app.db.schema import init_db
from server.app.db.transaction import read_connection
from server.app.executors.config import RemoteCapabilityConfig, load_executor_definitions
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
    labels_satisfy,
)
from server.app.routes.remote import create_remote_router
from tests.postgres_support import TEST_DATABASE_URL

ADMIN_TOKEN = "admin-global-token"
ADMIN_HEADERS = {"X-Worker-Token": ADMIN_TOKEN}
REQUIRE_BIG_MEM = {"transcribe": {"mem_gb": ">=16"}}


def _broker(
    tmp_path: Path,
    requirements: dict[str, dict[str, str]] | None = None,
) -> RemoteExecutionBroker:
    db_path = TEST_DATABASE_URL
    init_db(db_path)
    return RemoteExecutionBroker(
        db_path,
        tmp_path / "bundles",
        claim_timeout_seconds=60.0,
        capability_label_requirements=requirements,
    )


def _register(
    broker: RemoteExecutionBroker,
    worker_id: str,
    capabilities: list[str],
    *,
    slots: int = 4,
    labels: dict | None = None,
) -> None:
    broker.issue_worker_token(worker_id, worker_id, capabilities, slots, labels)


def _submit(broker: RemoteExecutionBroker, execution_id: str, capability: str) -> None:
    broker.submit(
        RemoteExecutionPayload(
            execution_id=execution_id,
            lease_id=f"lease-{execution_id}",
            job_id="job1",
            node_key="node_a",
            capability=capability,
            bundle_name=f"{execution_id}.tar.gz",
            manifest={"job_id": "job1", "node_key": "node_a"},
        )
    )


# ---- matrix 1: capabilities without requires_labels keep current behavior ----


def test_no_requirements_any_worker_dequeues(tmp_path: Path) -> None:
    broker = _broker(tmp_path)  # no requirements configured at all
    _register(broker, "w1", ["cap_a"])  # no labels
    _submit(broker, "e1", "cap_a")
    claim = broker.dequeue("w1", {"cap_a"})
    assert claim is not None
    assert claim.execution_id == "e1"


def test_unconstrained_capability_ignores_worker_labels(tmp_path: Path) -> None:
    broker = _broker(tmp_path, REQUIRE_BIG_MEM)
    _register(broker, "w1", ["cap_a"])  # no labels
    _submit(broker, "e1", "cap_a")  # cap_a has no requires_labels
    assert broker.dequeue("w1", {"cap_a"}) is not None


# ---- matrix 2: numeric constraint satisfied ----


def test_numeric_constraint_satisfied_dequeues(tmp_path: Path) -> None:
    broker = _broker(tmp_path, REQUIRE_BIG_MEM)
    _register(broker, "w-big", ["transcribe"], labels={"mem_gb": 32})
    _submit(broker, "e1", "transcribe")
    claim = broker.dequeue("w-big", {"transcribe"})
    assert claim is not None
    assert claim.execution_id == "e1"


# ---- matrix 3: numeric constraint violated -> skipped ----


def test_numeric_constraint_violated_returns_none(tmp_path: Path) -> None:
    broker = _broker(tmp_path, REQUIRE_BIG_MEM)
    _register(broker, "w-small", ["transcribe"], labels={"mem_gb": 8})
    _submit(broker, "e1", "transcribe")
    assert broker.dequeue("w-small", {"transcribe"}) is None


# ---- matrix 4: unknown label never satisfies a constraint ----


def test_missing_label_returns_none(tmp_path: Path) -> None:
    broker = _broker(tmp_path, REQUIRE_BIG_MEM)
    _register(broker, "w-nolabel", ["transcribe"], labels={"device": "mac-mini"})
    _submit(broker, "e1", "transcribe")
    assert broker.dequeue("w-nolabel", {"transcribe"}) is None


# ---- matrix 5: literal exact match ----


def test_literal_constraint_match_and_mismatch(tmp_path: Path) -> None:
    broker = _broker(tmp_path, {"transcribe": {"device": "mac-mini"}})
    _register(broker, "w-mac", ["transcribe"], labels={"device": "mac-mini"})
    _register(broker, "w-pc", ["transcribe"], labels={"device": "gpu-box"})
    _submit(broker, "e1", "transcribe")
    assert broker.dequeue("w-pc", {"transcribe"}) is None
    claim = broker.dequeue("w-mac", {"transcribe"})
    assert claim is not None
    assert claim.execution_id == "e1"


# ---- matrix 6: multiple constraints combine with AND semantics ----


def test_multiple_constraints_require_all(tmp_path: Path) -> None:
    requirements = {"transcribe": {"mem_gb": ">=16", "device": "mac-mini"}}
    broker = _broker(tmp_path, requirements)
    _register(broker, "w-full", ["transcribe"], labels={"mem_gb": 32, "device": "mac-mini"})
    _register(broker, "w-half", ["transcribe"], labels={"mem_gb": 32})  # device missing
    _register(broker, "w-wrong", ["transcribe"], labels={"mem_gb": 32, "device": "gpu-box"})
    _submit(broker, "e1", "transcribe")
    assert broker.dequeue("w-half", {"transcribe"}) is None
    assert broker.dequeue("w-wrong", {"transcribe"}) is None
    assert broker.dequeue("w-full", {"transcribe"}) is not None


# ---- matrix 7: mixed candidates — only the satisfying capability is handed out ----


def test_mixed_candidates_only_satisfying_capability_claimed(tmp_path: Path) -> None:
    broker = _broker(tmp_path, {"cap_big": {"mem_gb": ">=16"}})
    _register(broker, "w1", ["cap_big", "cap_small"])  # no labels
    _submit(broker, "e-big", "cap_big")  # older, but constrained
    _submit(broker, "e-small", "cap_small")
    claim = broker.dequeue("w1", {"cap_big", "cap_small"})
    assert claim is not None
    assert claim.execution_id == "e-small"
    # The constrained row stays queued for a worker that satisfies it.
    _register(broker, "w-big", ["cap_big"], labels={"mem_gb": 64})
    claim = broker.dequeue("w-big", {"cap_big"})
    assert claim is not None
    assert claim.execution_id == "e-big"


# ---- matrix 8: FIFO is preserved among satisfying workers ----


def test_fifo_preserved_and_unsatisfying_worker_never_wins(tmp_path: Path) -> None:
    broker = _broker(tmp_path, REQUIRE_BIG_MEM)
    _register(broker, "w-small", ["transcribe"], labels={"mem_gb": 8})
    _register(broker, "w-big", ["transcribe"], labels={"mem_gb": 32})
    _submit(broker, "e1", "transcribe")
    _submit(broker, "e2", "transcribe")
    assert broker.dequeue("w-small", {"transcribe"}) is None
    first = broker.dequeue("w-big", {"transcribe"})
    assert first is not None and first.execution_id == "e1"
    assert broker.dequeue("w-small", {"transcribe"}) is None
    second = broker.dequeue("w-big", {"transcribe"})
    assert second is not None and second.execution_id == "e2"


# ---- matrix 9: slots cap still applies on top of satisfied constraints ----


def test_slots_cap_stacks_with_label_constraints(tmp_path: Path) -> None:
    broker = _broker(tmp_path, REQUIRE_BIG_MEM)
    _register(broker, "w-big", ["transcribe"], slots=1, labels={"mem_gb": 32})
    _register(broker, "w-big2", ["transcribe"], labels={"mem_gb": 16})
    _submit(broker, "e1", "transcribe")
    _submit(broker, "e2", "transcribe")
    assert broker.dequeue("w-big", {"transcribe"}) is not None  # fills the single slot
    # Labels still satisfy, but the slots cap (phase 2 semantics) refuses.
    assert broker.dequeue("w-big", {"transcribe"}) is None
    # Another satisfying worker with free slots still gets the row.
    claim = broker.dequeue("w-big2", {"transcribe"})
    assert claim is not None and claim.execution_id == "e2"


# ---- matrix 10: invalid constraint values are rejected at config load ----


@pytest.mark.parametrize("bad", [">>16", ">=", ">=1.5", ">16", "", ">= 16", ">=-4"])
def test_requires_labels_rejects_invalid_constraint_values(bad: str) -> None:
    with pytest.raises(ValidationError):
        RemoteCapabilityConfig(skill="some/skill", requires_labels={"mem_gb": bad})


@pytest.mark.parametrize("good", [">=16", ">=0", "mac-mini", "gpu-box-2"])
def test_requires_labels_accepts_valid_constraint_values(good: str) -> None:
    config = RemoteCapabilityConfig(skill="some/skill", requires_labels={"mem_gb": good})
    assert config.requires_labels == {"mem_gb": good}


def test_requires_labels_invalid_value_fails_executor_definition_load() -> None:
    raw = {
        "remote-1": {
            "kind": "remote",
            "global_capacity": 2,
            "capabilities": {
                "transcribe": {"skill": "video/transcribe", "requires_labels": {"mem_gb": ">>16"}}
            },
        }
    }
    with pytest.raises(ValidationError):
        load_executor_definitions(raw)


# ---- labels_satisfy: the pure constraint evaluator ----


def test_labels_satisfy_empty_requirements_always_true() -> None:
    assert labels_satisfy({}, {}) is True
    assert labels_satisfy({"mem_gb": 1}, {}) is True


@pytest.mark.parametrize(
    ("labels", "requirements", "expected"),
    [
        ({"mem_gb": 32}, {"mem_gb": ">=16"}, True),
        ({"mem_gb": 16}, {"mem_gb": ">=16"}, True),  # boundary is inclusive
        ({"mem_gb": 8}, {"mem_gb": ">=16"}, False),
        ({"mem_gb": "32"}, {"mem_gb": ">=16"}, True),  # numeric strings compare numerically
        ({"mem_gb": "lots"}, {"mem_gb": ">=16"}, False),  # non-numeric never satisfies >=
        ({"mem_gb": None}, {"mem_gb": ">=16"}, False),  # unknown label
        ({}, {"mem_gb": ">=16"}, False),
        ({"device": "mac-mini"}, {"device": "mac-mini"}, True),
        ({"device": "gpu-box"}, {"device": "mac-mini"}, False),
        ({"gpu": True}, {"gpu": "True"}, True),  # scalars compare as strings
        ({"mem_gb": 32, "device": "mac-mini"}, {"mem_gb": ">=16", "device": "mac-mini"}, True),
        ({"mem_gb": 8, "device": "mac-mini"}, {"mem_gb": ">=16", "device": "mac-mini"}, False),
    ],
)
def test_labels_satisfy_cases(labels: dict, requirements: dict[str, str], expected: bool) -> None:
    assert labels_satisfy(labels, requirements) is expected


# ---- claim-reported labels: the route upserts remote_workers.labels_json ----


def _remote_settings(settings):
    remote = settings.executor_runtime.remote.model_copy(update={"worker_token": ADMIN_TOKEN})
    runtime = settings.executor_runtime.model_copy(update={"remote": remote})
    return dataclasses.replace(settings, executor_runtime=runtime)


@pytest.fixture
def rig(tmp_path: Path, settings):
    db_path = TEST_DATABASE_URL
    init_db(db_path)
    broker = RemoteExecutionBroker(
        db_path, tmp_path / "bundles", capability_label_requirements=REQUIRE_BIG_MEM
    )
    app = FastAPI()
    app.include_router(create_remote_router(broker, _remote_settings(settings)), prefix="/api")
    return TestClient(app), broker, db_path


def _register_via_api(client: TestClient, worker_id: str, **overrides) -> str:
    body = {
        "worker_id": worker_id,
        "name": worker_id,
        "capabilities": ["transcribe"],
        "slots": 4,
        **overrides,
    }
    resp = client.post("/api/remote/workers/register", json=body, headers=ADMIN_HEADERS)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["worker_token"])


def _claim_via_api(client: TestClient, worker_id: str, token: str, **overrides):
    body = {
        "worker_id": worker_id,
        "capabilities": ["transcribe"],
        "worker_version": 1,
        **overrides,
    }
    return client.post(
        "/api/remote/claim",
        json=body,
        headers={"X-Worker-Token": token, "X-Worker-Id": worker_id},
    )


def _labels_json(db_path: str, worker_id: str) -> dict:
    with read_connection(db_path) as conn:
        row = conn.execute(
            "select labels_json from remote_workers where worker_id = ?", (worker_id,)
        ).fetchone()
    assert row is not None
    return json.loads(row["labels_json"])


def test_claim_with_labels_updates_registry(rig) -> None:
    client, _, db_path = rig
    token = _register_via_api(client, "w1")
    assert _labels_json(db_path, "w1") == {}
    resp = _claim_via_api(client, "w1", token, labels={"mem_gb": 32})
    assert resp.status_code == 204  # empty queue, but labels still land
    assert _labels_json(db_path, "w1") == {"mem_gb": 32}


def test_claim_without_labels_keeps_registered_labels(rig) -> None:
    client, _, db_path = rig
    token = _register_via_api(client, "w1", labels={"device": "mac-mini"})
    assert _claim_via_api(client, "w1", token).status_code == 204
    assert _labels_json(db_path, "w1") == {"device": "mac-mini"}


@pytest.mark.parametrize("bad_value", [{"nested": 1}, ["list"], None])
def test_claim_rejects_non_scalar_labels(rig, bad_value) -> None:
    client, _, _ = rig
    token = _register_via_api(client, "w1")
    resp = _claim_via_api(client, "w1", token, labels={"bad": bad_value})
    assert resp.status_code == 400


def test_claim_reported_labels_drive_routing_end_to_end(rig) -> None:
    """A worker that upgrades its labels via claim becomes eligible without
    re-registering: small worker is skipped, big worker (labels from claim)
    gets the constrained row."""
    client, broker, _ = rig
    small = _register_via_api(client, "w-small", labels={"mem_gb": 8})
    big = _register_via_api(client, "w-big", labels={"mem_gb": 8})
    _submit(broker, "e1", "transcribe")
    assert _claim_via_api(client, "w-small", small).status_code == 204
    # w-big reports more memory on its next poll and immediately qualifies.
    resp = _claim_via_api(client, "w-big", big, labels={"mem_gb": 32})
    assert resp.status_code == 200
    assert resp.json()["execution_id"] == "e1"


# ---- worker.py: --label parsing and claim/register reporting ----


def test_worker_parse_labels() -> None:
    from scripts.remote.worker import _parse_labels

    assert _parse_labels(["mem_gb=32", "device=mac-mini"]) == {"mem_gb": "32", "device": "mac-mini"}
    assert _parse_labels([]) == {}
    with pytest.raises(ValueError, match="--label"):
        _parse_labels(["no-equals-sign"])
    with pytest.raises(ValueError, match="--label"):
        _parse_labels(["=value"])


def test_worker_client_claim_reports_labels() -> None:
    from scripts.remote.worker import WorkerClient

    client = WorkerClient("http://server", "token", "w1")
    seen: dict[str, object] = {}

    def fake_request(method, path, *, body=None, headers=None):
        seen["body"] = body
        return 204, b""

    client._request = fake_request  # type: ignore[method-assign]
    assert client.claim(["transcribe"], {"mem_gb": "32"}) is None
    assert json.loads(seen["body"]) == {
        "worker_id": "w1",
        "capabilities": ["transcribe"],
        "labels": {"mem_gb": "32"},
        "worker_version": 1,
    }


def test_worker_client_register_worker_reports_labels() -> None:
    from scripts.remote.worker import WorkerClient

    client = WorkerClient("http://server", "management-token", "w1")
    seen: dict[str, object] = {}

    def fake_request(method, path, *, body=None, headers=None):
        seen["body"] = body
        return 201, b'{"worker_token": "w1.secret"}'

    client._request = fake_request  # type: ignore[method-assign]
    client.register_worker("Mac mini", ["transcribe"], 4, {"mem_gb": "32"})
    assert json.loads(seen["body"])["labels"] == {"mem_gb": "32"}
