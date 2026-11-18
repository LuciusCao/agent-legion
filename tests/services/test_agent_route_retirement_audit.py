"""Full-history Agent-route retirement classification over isolated PostgreSQL (#936)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

from scripts.agent_route_retirement_audit import main as route_audit_main
from server.app.agent_catalog import AgentDefinition
from server.app.db.transaction import read_connection
from server.app.jobs import JobQueries
from server.app.jobs.queries.agent_backfill_reader import agent_backfill_reader_from_dsn
from server.app.services.agent_route_retirement_audit import (
    build_agent_route_retirement_audit,
)
from server.app.workflows.revision_format import serialize_definition
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import replace_agent_catalog, save_agent_definition_draft
from tests.postgres_support import TEST_DATABASE_URL

_WS = "ws_route_audit"


def _agent(capability: str = "generate", **overrides: Any) -> AgentDefinition:
    return AgentDefinition.model_validate(
        {"capability": capability, "runtime": "velites", **overrides}
    )


def _snapshot(nodes: dict[str, WorkflowNode]) -> str:
    return serialize_definition(
        WorkflowDefinition(key=_WS, label="Audit", intake=WorkflowIntake(), nodes=nodes)
    )


def _legacy(key: str, capability: str = "generate") -> WorkflowNode:
    return WorkflowNode(key=key, label=key, capability=capability, node_type="agent")


@pytest.fixture
def queries(tmp_path: Path) -> JobQueries:
    db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    db.create_workspace("Route audit", workspace_id=_WS)
    return db


def _job(
    db: JobQueries,
    source_id: str,
    nodes: dict[str, WorkflowNode],
    *,
    run_id: str = "",
    snapshot: str | None = None,
) -> dict[str, Any]:
    return db.create_job(
        workflow_key=_WS,
        source_type="test",
        source_id=source_id,
        run_id=run_id,
        title=source_id,
        node_keys=list(nodes),
        workspace_id=_WS,
        workflow_revision_id=f"rev-{source_id}",
        workflow_version=1,
        workflow_definition_hash=f"hash-{source_id}",
        workflow_definition_snapshot_json=_snapshot(nodes) if snapshot is None else snapshot,
    )


def _route(db: JobQueries, node_key: str, target_id: str, *, target_kind: str = "agent") -> None:
    with db.connect() as conn:
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
            " values (%s, %s, %s, %s)",
            (_WS, node_key, target_kind, target_id),
        )


def _run(db: JobQueries, source_id: str, pins: dict[str, Any]) -> dict[str, Any]:
    return db.create_run(
        _WS,
        "quality_replay",
        {"source": source_id},
        _WS,
        frozen_pins={"agent_versions": pins, "node_code_versions": {}},
    )


def _request(
    db: JobQueries,
    execution_id: str,
    job_id: str,
    node_key: str,
    agent_id: str,
    definition_hash: str,
    *,
    version: int | None = None,
    profile_source: str = "agent_definition",
    workspace_id: str = _WS,
) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            insert into agent_execution_requests(
              execution_id, workspace_id, job_id, node_key, kind, agent_id,
              agent_definition_hash, node_concurrency_limit, queued_at, manifest_json,
              pinned_agent_version, profile_source
            ) values (%s, %s, %s, %s, 'agent', %s, %s, 1, current_timestamp, '{}', %s, %s)
            """,
            (
                execution_id,
                workspace_id,
                job_id,
                node_key,
                agent_id,
                definition_hash,
                version,
                profile_source,
            ),
        )


def _audit() -> dict[str, Any]:
    return build_agent_route_retirement_audit(agent_backfill_reader_from_dsn(TEST_DATABASE_URL))


def _only(report: dict[str, Any], source: str) -> dict[str, Any]:
    [item] = [item for item in report["items"] if item["source"] == source]
    return item


def test_equivalent_route_less_and_active_unresolved_are_safe(queries: JobQueries) -> None:
    replace_agent_catalog(_WS, {"writer": _agent()})
    _route(queries, "routed", "writer")
    _job(queries, "safe", {"routed": _legacy("routed"), "free": _legacy("free")})
    with queries.connect() as conn:
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, version, status, definition_json,"
            " definition_hash) values ('rev-active', %s, 1, 'active', %s, 'rev-hash')",
            (_WS, _snapshot({"free": _legacy("free")})),
        )

    report = _audit()

    assert report["route_retirement_blocked"] is False
    assert report["summary"]["blocking_items"] == 0
    assert report["summary"]["by_category"]["equivalent"] == 1
    assert report["summary"]["by_category"]["route_less_fallback"] == 2
    assert report["coverage"]["jobs"] == 1
    assert report["coverage"]["active_revisions"] == 1
    assert {item["source"] for item in report["items"]} == {"job_snapshot", "active_revision"}
    assert all(item["fallback_target"] == "writer" for item in report["items"])
    assert {
        (item["source"], item["node_key"]): item["route_present"] for item in report["items"]
    } == {
        ("job_snapshot", "routed"): True,
        ("job_snapshot", "free"): False,
        ("active_revision", "free"): False,
    }


@pytest.mark.parametrize(
    ("target_kind", "target_id"), [("handler_executor", "legacy"), ("agent", "")]
)
def test_existing_unusable_route_blocks_every_source_and_cli(
    queries: JobQueries, tmp_path: Path, target_kind: str, target_id: str
) -> None:
    replace_agent_catalog(_WS, {"writer": _agent()})
    pin = {"agent_id": "writer", "version": 1, "definition_hash": _agent().definition_hash()}
    run = _run(queries, "invalid-route", {"generate": pin})
    job = _job(queries, "invalid-route", {"generate": _legacy("generate")}, run_id=str(run["id"]))
    _route(queries, "generate", target_id, target_kind=target_kind)
    _request(
        queries,
        "req-invalid-route",
        str(job["id"]),
        "generate",
        "writer",
        pin["definition_hash"],
        version=1,
    )
    with queries.connect() as conn:
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, version, status, definition_json,"
            " definition_hash) values ('rev-invalid-route', %s, 1, 'active', %s, 'rev-hash')",
            (_WS, _snapshot({"generate": _legacy("generate")})),
        )

    report = _audit()

    assert len(report["items"]) == 4
    assert {item["source"] for item in report["items"]} == {
        "job_snapshot",
        "active_revision",
        "agent_version_pin",
        "queued_request",
    }
    assert all(item["category"] == "invalid_route" for item in report["items"])
    assert all(item["blocking"] and item["route_present"] for item in report["items"])
    output = tmp_path / "invalid-route.json"
    assert route_audit_main(["--database-url", TEST_DATABASE_URL, "--output", str(output)]) == 1
    cli_report = json.loads(output.read_text(encoding="utf-8"))
    assert cli_report["summary"]["by_category"]["invalid_route"] == 4


def test_identity_changes_and_unreadable_snapshots_all_block(queries: JobQueries) -> None:
    replace_agent_catalog(_WS, {"current": _agent(), "other": _agent("other"), "empty": _agent("")})
    save_agent_definition_draft(TEST_DATABASE_URL, _WS, "drafted", _agent())
    for node, target in (("drift", "other"), ("missing", "gone"), ("unpublished", "drafted")):
        _route(queries, node, target)
    _job(
        queries,
        "danger",
        {
            "drift": _legacy("drift"),
            "missing": _legacy("missing"),
            "unpublished": _legacy("unpublished"),
            "empty": _legacy("empty", ""),
            "absent": _legacy("absent", "absent"),
        },
    )
    _job(queries, "malformed", {}, snapshot="{not-json-secret-token")
    _job(queries, "missing-snapshot", {}, snapshot="")

    report = _audit()

    by_node = {
        item["node_key"]: item for item in report["items"] if item["source"] == "job_snapshot"
    }
    assert (by_node["drift"]["category"], by_node["drift"]["blocking"]) == ("drift", True)
    assert by_node["drift"]["fallback_target"] == "current"
    assert by_node["missing"]["category"] == "missing_target"
    assert by_node["unpublished"]["category"] == "unpublished_target"
    assert by_node["empty"]["reason"] == "empty_capability"
    assert by_node["absent"]["category"] == "capability_unresolved"
    assert report["summary"]["by_category"]["malformed_snapshot"] == 1
    assert report["summary"]["by_category"]["missing_snapshot"] == 1
    assert report["route_retirement_blocked"] is True
    assert all(item["blocking"] for item in report["items"])
    encoded = json.dumps(report)
    assert "secret-token" not in encoded
    assert "workflow_definition_snapshot_json" not in encoded


def test_exact_historical_pin_and_queued_request_are_equivalent(queries: JobQueries) -> None:
    replace_agent_catalog(_WS, {"writer": _agent(runtime="pi")})
    drafted = save_agent_definition_draft(
        TEST_DATABASE_URL, _WS, "writer", _agent(requires_labels={"arch": "arm64"})
    )
    pin = {"agent_id": "writer", "version": 2, "definition_hash": drafted.definition_hash}
    run = _run(queries, "pinned", {"generate": pin})
    job = _job(queries, "pinned", {"generate": _legacy("generate")}, run_id=str(run["id"]))
    _request(
        queries,
        "req-pinned",
        str(job["id"]),
        "generate",
        "writer",
        drafted.definition_hash,
        version=2,
    )

    report = _audit()

    assert report["route_retirement_blocked"] is False
    assert report["coverage"]["agent_version_pins"] == 1
    assert report["coverage"]["queued_legacy_agent_requests"] == 1
    assert report["summary"]["by_source"] == {
        "job_snapshot": 1,
        "active_revision": 0,
        "agent_version_pin": 1,
        "queued_request": 1,
    }
    assert all(item["category"] == "route_less_fallback" for item in report["items"])
    assert _only(report, "agent_version_pin")["pin"] == pin


def test_pin_hash_mutation_blocks_job_pin_and_queued_request(queries: JobQueries) -> None:
    replace_agent_catalog(_WS, {"writer": _agent()})
    pin = {"agent_id": "writer", "version": 1, "definition_hash": _agent().definition_hash()}
    run = _run(queries, "mutated", {"generate": pin})
    job = _job(queries, "mutated", {"generate": _legacy("generate")}, run_id=str(run["id"]))
    _request(
        queries,
        "req-mutated",
        str(job["id"]),
        "generate",
        "writer",
        pin["definition_hash"],
        version=1,
    )
    assert _audit()["route_retirement_blocked"] is False
    with queries.connect() as conn:
        conn.execute(
            "update runs set frozen_pins_json=%s where id=%s",
            (
                json.dumps(
                    {"agent_versions": {"generate": {**pin, "definition_hash": "secret-pin-hash"}}}
                ),
                run["id"],
            ),
        )

    report = _audit()

    assert report["route_retirement_blocked"] is True
    assert report["summary"]["blocking_items"] == 3
    assert {item["source"] for item in report["items"]} == {
        "job_snapshot",
        "agent_version_pin",
        "queued_request",
    }
    assert all(item["category"] == "pin_mismatch" for item in report["items"])
    reasons = {item["source"]: item["reason"] for item in report["items"]}
    assert reasons == {
        "job_snapshot": "invalid_definition_hash",
        "agent_version_pin": "invalid_definition_hash",
        "queued_request": "invalid_definition_hash",
    }
    assert "secret-pin-hash" not in json.dumps(report)


def test_queued_request_workspace_mismatch_blocks(queries: JobQueries) -> None:
    replace_agent_catalog(_WS, {"writer": _agent()})
    queries.create_workspace("Other", workspace_id="ws_other")
    job = _job(queries, "cross-workspace", {"generate": _legacy("generate")})
    _request(
        queries,
        "req-cross-workspace",
        str(job["id"]),
        "generate",
        "writer",
        _agent().definition_hash(),
        workspace_id="ws_other",
    )

    report = _audit()

    request = _only(report, "queued_request")
    assert (request["category"], request["reason"], request["blocking"]) == (
        "pin_mismatch",
        "request_workspace_mismatch",
        True,
    )
    assert report["summary"]["blocking_items"] == 1


def test_queued_request_version_mutation_blocks_even_with_valid_run_pin(
    queries: JobQueries,
) -> None:
    replace_agent_catalog(_WS, {"writer": _agent()})
    drafted = save_agent_definition_draft(TEST_DATABASE_URL, _WS, "writer", _agent(runtime="pi"))
    pin = {"agent_id": "writer", "version": 2, "definition_hash": drafted.definition_hash}
    run = _run(queries, "request-mutation", {"generate": pin})
    job = _job(
        queries, "request-mutation", {"generate": _legacy("generate")}, run_id=str(run["id"])
    )
    _request(
        queries,
        "req-version-mutated",
        str(job["id"]),
        "generate",
        "writer",
        drafted.definition_hash,
        version=1,
    )

    report = _audit()

    request = _only(report, "queued_request")
    assert (request["category"], request["reason"], request["blocking"]) == (
        "pin_mismatch",
        "request_version_mismatch",
        True,
    )
    assert report["summary"]["blocking_items"] == 1


def test_orphan_and_malformed_pins_fail_closed(queries: JobQueries) -> None:
    replace_agent_catalog(_WS, {"writer": _agent()})
    pin = {"agent_id": "writer", "version": 1, "definition_hash": _agent().definition_hash()}
    _run(queries, "orphan", {"generate": pin})
    malformed = _run(queries, "malformed", {})
    _job(queries, "malformed-pins", {"generate": _legacy("generate")}, run_id=str(malformed["id"]))
    with queries.connect() as conn:
        conn.execute(
            "update runs set frozen_pins_json='{bad-json-secret-value' where id=%s",
            (malformed["id"],),
        )

    report = _audit()

    assert report["route_retirement_blocked"] is True
    assert report["coverage"]["agent_version_pins"] == 1
    assert report["coverage"]["runs_with_invalid_agent_pins"] == 1
    reasons = {item["reason"] for item in report["items"]}
    assert {"no_readable_job", "malformed_frozen_pins", "malformed_run_pins"} <= reasons
    assert "secret-value" not in json.dumps(report)


class _InjectedReader:
    def __init__(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self._data = data

    def agent_route_retirement_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return self._data


def _definition_row(agent_id: str, definition: AgentDefinition) -> dict[str, Any]:
    return {
        "workspace_id": _WS,
        "entity_key": agent_id,
        "version": 1,
        "status": "published",
        "definition_json": json.dumps(definition.model_dump(mode="json")),
        "definition_hash": definition.definition_hash(),
    }


def _hash_fault_data(
    hash_value: Any, *, with_pin: bool, invalid_definition: bool = False
) -> dict[str, list[dict[str, Any]]]:
    definition_row = _definition_row("writer", _agent())
    definition_row["definition_hash"] = hash_value
    if invalid_definition:
        definition_row["definition_json"] = json.dumps(
            {"capability": "generate", "runtime": "invalid", "secret": "definition-secret"}
        )
    pin = {"agent_id": "writer", "version": 1, "definition_hash": hash_value}
    run_id = "run-hash" if with_pin else ""
    return {
        "jobs": [
            {
                "id": "job-hash",
                "workspace_id": _WS,
                "run_id": run_id,
                "status": "queued",
                "workflow_revision_id": "rev-old",
                "workflow_definition_snapshot_json": _snapshot({"generate": _legacy("generate")}),
            }
        ],
        "active_revisions": [],
        "runs": [
            {
                "id": run_id,
                "workspace_id": _WS,
                "status": "created",
                "frozen_pins_json": json.dumps({"agent_versions": {"generate": pin}}),
            }
        ]
        if with_pin
        else [],
        "routes": [],
        "agent_versions": [definition_row],
        "queued_requests": [
            {
                "execution_id": "req-hash",
                "workspace_id": _WS,
                "job_id": "job-hash",
                "node_key": "generate",
                "kind": "agent",
                "profile_source": "agent_definition",
                "agent_id": "writer",
                "agent_definition_hash": hash_value,
                "pinned_agent_version": 1 if with_pin else None,
            }
        ],
    }


def test_fault_injected_illegal_route_kind_blocks() -> None:
    data = _hash_fault_data(_agent().definition_hash(), with_pin=False)
    data["queued_requests"] = []
    data["routes"] = [
        {
            "workspace_id": _WS,
            "node_key": "generate",
            "target_kind": "not-a-real-kind",
            "target_id": "writer",
        }
    ]

    report = build_agent_route_retirement_audit(_InjectedReader(data))  # type: ignore[arg-type]

    item = _only(report, "job_snapshot")
    assert (item["category"], item["reason"], item["blocking"]) == (
        "invalid_route",
        "route_kind_not_agent",
        True,
    )


@pytest.mark.parametrize(
    ("hash_value", "invalid_definition"),
    [
        ("NOT-A-HASH-SECRET", True),
        ("A" * 64, False),
        ("", False),
        (None, False),
        ({"hash": "secret-object"}, False),
        (8675309, False),
    ],
)
def test_equal_malformed_stored_pin_and_request_hashes_all_block(
    hash_value: Any, invalid_definition: bool
) -> None:
    report = build_agent_route_retirement_audit(
        _InjectedReader(
            _hash_fault_data(hash_value, with_pin=True, invalid_definition=invalid_definition)
        )  # type: ignore[arg-type]
    )

    assert report["route_retirement_blocked"] is True
    assert len(report["items"]) == 3
    assert all(item["category"] == "pin_mismatch" for item in report["items"])
    reasons = {item["source"]: item["reason"] for item in report["items"]}
    assert reasons == {
        "job_snapshot": "invalid_definition_hash",
        "agent_version_pin": "invalid_definition_hash",
        "queued_request": "request_definition_hash_invalid",
    }
    encoded = json.dumps(report)
    if isinstance(hash_value, str) and hash_value:
        assert hash_value not in encoded
    elif isinstance(hash_value, int):
        assert str(hash_value) not in encoded
    assert "definition-secret" not in encoded
    assert "secret-object" not in encoded
    assert "<invalid>" in encoded


def test_malformed_stored_and_unpinned_request_hash_block_without_a_pin() -> None:
    report = build_agent_route_retirement_audit(
        _InjectedReader(  # type: ignore[arg-type]
            _hash_fault_data("STORED-HASH-SECRET", with_pin=False, invalid_definition=True)
        )
    )

    assert report["route_retirement_blocked"] is True
    reasons = {item["source"]: item["reason"] for item in report["items"]}
    assert reasons == {
        "job_snapshot": "stored_definition_hash_invalid",
        "queued_request": "request_definition_hash_invalid",
    }
    assert "STORED-HASH-SECRET" not in json.dumps(report)
    assert "definition-secret" not in json.dumps(report)


@pytest.mark.parametrize("with_pin", [False, True])
def test_only_queued_request_hash_is_malformed_still_blocks(with_pin: bool) -> None:
    data = _hash_fault_data(_agent().definition_hash(), with_pin=with_pin)
    data["queued_requests"][0]["agent_definition_hash"] = "REQUEST-HASH-SECRET"

    report = build_agent_route_retirement_audit(_InjectedReader(data))  # type: ignore[arg-type]

    assert report["route_retirement_blocked"] is True
    assert report["summary"]["blocking_items"] == 1
    request = _only(report, "queued_request")
    assert (request["category"], request["reason"], request["blocking"]) == (
        "pin_mismatch",
        "request_definition_hash_invalid",
        True,
    )
    assert "REQUEST-HASH-SECRET" not in json.dumps(report)


def test_fault_injected_ambiguous_capability_blocks_route_less_fallback() -> None:
    job = {
        "id": "job-ambiguous",
        "workspace_id": _WS,
        "run_id": "",
        "status": "queued",
        "workflow_revision_id": "rev-old",
        "workflow_definition_snapshot_json": _snapshot({"generate": _legacy("generate")}),
    }
    data = {
        "jobs": [job],
        "active_revisions": [],
        "runs": [],
        "routes": [],
        "agent_versions": [_definition_row("a", _agent()), _definition_row("b", _agent())],
        "queued_requests": [],
    }

    report = build_agent_route_retirement_audit(_InjectedReader(data))  # type: ignore[arg-type]

    item = _only(report, "job_snapshot")
    assert (item["category"], item["reason"], item["blocking"]) == (
        "capability_unresolved",
        "ambiguous_capability",
        True,
    )
    assert item["fallback_candidates"] == ["a", "b"]
    assert report["route_retirement_blocked"] is True


def _fingerprint() -> dict[str, str]:
    with read_connection(TEST_DATABASE_URL) as conn:
        tables = [
            str(row["table_name"])
            for row in conn.execute(
                "select table_name from information_schema.tables"
                " where table_schema=current_schema() and table_type='BASE TABLE'"
                " order by table_name"
            ).fetchall()
        ]
        result: dict[str, str] = {}
        for table in tables:
            row = conn.execute(
                "select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) as h"
                f' from "{table}" t'
            ).fetchone()
            assert row is not None
            result[table] = str(row["h"])
        return result


def test_cli_is_read_only_and_exit_one_means_blocked(
    queries: JobQueries,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "route-audit.json"

    def _no_init_db(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("audit must not initialize or migrate the schema")

    monkeypatch.setattr("server.app.jobs.queries.base.init_db", _no_init_db)
    before = _fingerprint()

    assert route_audit_main(["--database-url", TEST_DATABASE_URL, "--output", str(output)]) == 0

    clean = json.loads(output.read_text(encoding="utf-8"))
    assert clean["mode"] == "read-only-local"
    assert clean["route_retirement_blocked"] is False
    assert clean["coverage"]["jobs"] == 0
    assert _fingerprint() == before
    assert "no changes applied" in capsys.readouterr().err

    queries.create_job(
        workflow_key=_WS,
        source_type="test",
        source_id="missing-history",
        run_id="",
        title="local fixture",
        node_keys=[],
        workspace_id=_WS,
    )
    before_blocked = _fingerprint()

    assert route_audit_main(["--database-url", TEST_DATABASE_URL, "--output", str(output)]) == 1

    blocked = json.loads(output.read_text(encoding="utf-8"))
    assert blocked["route_retirement_blocked"] is True
    assert blocked["summary"]["by_category"]["missing_snapshot"] == 1
    assert blocked["items"][0]["reason"] == "missing_snapshot"
    assert _fingerprint() == before_blocked


def test_audit_reader_server_rejects_writes(queries: JobQueries) -> None:
    reader = agent_backfill_reader_from_dsn(TEST_DATABASE_URL)

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        reader.upsert_workspace_workflow_draft(_WS, "nodes: {}")

    assert queries.get_workspace_workflow_draft(_WS) is None


def test_cli_stdout_is_only_deterministic_json(
    queries: JobQueries, capsys: pytest.CaptureFixture[str]
) -> None:
    assert route_audit_main(["--database-url", TEST_DATABASE_URL]) == 0
    first = capsys.readouterr().out
    assert route_audit_main(["--database-url", TEST_DATABASE_URL]) == 0
    second = capsys.readouterr().out

    assert first == second
    assert json.loads(first)["report"] == "agent-route-retirement-audit"
    assert "postgresql://" not in first
