"""RUN-FREEZE-001: the legacy batch payload is rebuilt from run/job columns.

The dispatch chain and the node SDK keep their payload-shaped contract, but
the content comes only from ``runs.frozen_pins_json`` / ``jobs.input_json`` /
``jobs.frozen_config_json`` — never from a stored batch payload.
"""

from __future__ import annotations

import json

import pytest

from server.app.services.agent_version_pins import agent_version_pin
from server.app.services.node_code_pins import frozen_dispatch_pin
from server.app.services.node_config_batch import frozen_node_config, run_frozen_payload
from server.app.services.run_payload import (
    candidate_input,
    reconstruct_batch_payload,
    sdk_batch_row,
)

pytestmark = pytest.mark.no_db


def _run(pins: dict) -> dict:
    return {"id": "run-1", "frozen_pins_json": json.dumps(pins)}


def _job(input_doc: dict | None, frozen_config: dict | None) -> dict:
    return {
        "id": "j1",
        "run_id": "run-1",
        "input_json": json.dumps(input_doc) if input_doc is not None else None,
        "frozen_config_json": json.dumps(frozen_config) if frozen_config is not None else None,
    }


def test_candidate_input_builds_legacy_ref() -> None:
    assert candidate_input(
        {"entity_id": "Q1", "entity_type": "question", "title": "T1", "stem": "s1"}
    ) == {
        "type": "ref",
        "connection_key": "",
        "external_id": "Q1",
        "legacy": True,
        "entity_type": "question",
        "title": "T1",
        "stem": "s1",
    }
    # Optional display fields drop out when empty.
    assert candidate_input({"entity_id": "Q2", "entity_type": "question", "title": ""}) == {
        "type": "ref",
        "connection_key": "",
        "external_id": "Q2",
        "legacy": True,
        "entity_type": "question",
    }


def test_candidate_input_passes_through_explicit_item() -> None:
    """Items-API candidates carry their terminal input document verbatim."""
    item = {"type": "material", "material_id": "mat-1"}
    assert candidate_input({"entity_id": "mat-1", "entity_type": "material", "input": item}) == item
    assert item == {"type": "material", "material_id": "mat-1"}  # defensive copy


def test_reconstruct_batch_payload_merges_pins_and_job_columns() -> None:
    pins = {
        "node_code_versions": {"n1": {"version": 3}},
        "agent_versions": {"n1": {"agent_id": "a", "version": 2}},
        "quality_replay": {"replay_id": "r1"},
    }
    payload = reconstruct_batch_payload(_run(pins), _job({"type": "ref"}, {"n1": {"alpha": 1}}))
    assert payload["node_code_versions"] == {"n1": {"version": 3}}
    assert payload["agent_versions"] == {"n1": {"agent_id": "a", "version": 2}}
    assert payload["quality_replay"] == {"replay_id": "r1"}
    assert payload["node_config"] == {"n1": {"alpha": 1}}
    assert payload["task_candidates"] == [{"type": "ref"}]


def test_reconstruct_batch_payload_degrades_without_run_or_columns() -> None:
    # No run row (legacy job without one): job columns alone.
    payload = reconstruct_batch_payload(None, _job(None, None))
    assert payload == {"node_config": {}, "task_candidates": []}
    # Corrupt column JSON degrades to empty instead of raising.
    payload = reconstruct_batch_payload(
        {"frozen_pins_json": "not-json{"},
        {"input_json": "{", "frozen_config_json": "{"},
    )
    assert payload == {"node_config": {}, "task_candidates": []}


def test_sdk_batch_row_synthesizes_the_legacy_payload_key() -> None:
    row = sdk_batch_row(
        _run({"node_code_versions": {"n1": {"version": 1}}}),
        _job({"type": "ref", "external_id": "Q1"}, {"n1": {"alpha": 1}}),
    )
    assert row is not None
    assert row["id"] == "run-1"
    payload = json.loads(row["source_payload_json"])
    assert payload["node_config"] == {"n1": {"alpha": 1}}
    assert payload["task_candidates"] == [{"type": "ref", "external_id": "Q1"}]
    assert sdk_batch_row(None, _job(None, None)) is None


def test_pin_readers_resolve_from_the_reconstructed_payload() -> None:
    """Replay semantics equivalence: pins read from run columns, not payloads."""
    pins = {
        "quality_replay": {"replay_id": "r1"},
        "node_code_versions": {"n1": {"version": 7}},
        "agent_versions": {"n1": {"agent_id": "a", "version": 2, "definition_hash": "h"}},
    }
    payload = reconstruct_batch_payload(_run(pins), _job(None, {"n1": {"alpha": 1}}))
    assert frozen_dispatch_pin({}, payload, "n1") == {"version": 7}
    # Snapshot pins win over the run pins, same as the old batch payload path.
    assert frozen_dispatch_pin({"n1": {"version": 9}}, payload, "n1") == {"version": 9}
    # Without the replay marker nothing pins (ordinary dispatch).
    ordinary = reconstruct_batch_payload(
        _run({"node_code_versions": {"n1": {"v": 1}}}), _job(None, None)
    )
    assert frozen_dispatch_pin({}, ordinary, "n1") is None
    assert agent_version_pin(payload, "n1") == {
        "agent_id": "a",
        "version": 2,
        "definition_hash": "h",
    }
    assert frozen_node_config(payload, "n1") == {"alpha": 1}
    assert frozen_node_config(payload, "missing") is None


def test_run_frozen_payload_reads_run_and_job_columns() -> None:
    class _JobDb:
        def get_run(self, run_id: str) -> dict | None:
            assert run_id == "run-1"
            return _run({"node_code_versions": {"n1": {"version": 1}}})

    payload = run_frozen_payload(_JobDb(), _job({"type": "ref"}, {"n1": {"alpha": 1}}))
    assert payload is not None
    assert payload["node_config"] == {"n1": {"alpha": 1}}
    # No run reference, or a missing run row, degrades to None.
    assert run_frozen_payload(_JobDb(), {"id": "j", "run_id": ""}) is None

    class _MissingRunDb:
        def get_run(self, run_id: str) -> dict | None:
            return None

    assert run_frozen_payload(_MissingRunDb(), _job(None, None)) is None
