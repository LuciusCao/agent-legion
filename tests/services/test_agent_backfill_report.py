"""Agent backfill dry-run report over a fixture workspace (#934, #440).

Pins the deterministic report and the zero-write contract: the reader's
connections are server-enforced read-only, every table is byte-identical
after a run, and object storage is never constructed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.agent_backfill_dry_run import main as dry_run_main
from server.app.agent_catalog import AgentDefinition
from server.app.jobs import JobQueries
from server.app.jobs.queries.agent_backfill_reader import (
    agent_backfill_reader_from_dsn,
    read_only_dsn,
)
from server.app.services.agent_backfill_report import (
    build_agent_backfill_report,
    render_report_json,
)
from server.app.services.agent_service import AgentService, reset_published_agent_cache
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL

_TONE = {"type": "object", "properties": {"tone": {"type": "string", "default": "calm"}}}

_ACTIVE_YAML = """
key: ws_bf
label: Backfill
nodes:
  draft_a:
    type: agent
    capability: write
    outputs: [a.json]
  draft_b:
    type: agent
    capability: write
    tools: [bash]
    outputs: [b.json]
  review:
    type: agent
    capability: review
    skill: {key: grp/review, ref: v2}
    outputs: [r.json]
  retired:
    type: agent
    capability: legacy
    outputs: [l.json]
  pack:
    capability: pack
    outputs: [p.json]
"""

# The draft drops ``retired``, binds a node schema on ``review`` (the
# overwrite discards it) and adds a node whose Agent was never published.
_DRAFT_YAML = """
key: ws_bf
label: Backfill
nodes:
  draft_a:
    type: agent
    capability: write
    outputs: [a.json]
  review:
    type: agent
    capability: review
    skill: {key: grp/review, ref: v2}
    config_schema:
      type: object
      properties:
        strict: {type: boolean, default: true}
    outputs: [r.json]
  fresh:
    type: agent
    capability: drafted
    outputs: [f.json]
"""


def _agent(capability: str, **overrides) -> AgentDefinition:
    return AgentDefinition.model_validate(
        {"capability": capability, "runtime": "velites", **overrides}
    )


@pytest.fixture
def fixture_db(tmp_path: Path) -> JobQueries:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    queries.create_workspace("Backfill", default_workflow_key="ws_bf", workspace_id="ws_bf")
    queries.create_workspace("Empty", default_workflow_key="ws_empty", workspace_id="ws_empty")
    queries.create_workspace("Broken", default_workflow_key="ws_broken", workspace_id="ws_broken")
    # First catalog publishes ``old``; the second replaces it → ``old`` archived.
    replace_agent_catalog("ws_bf", {"old": _agent("legacy")})
    replace_agent_catalog(
        "ws_bf",
        {
            "writer": _agent(
                "write",
                runtime="pi",
                tools=("read",),
                skill="grp/writer",
                requires_labels={"gpu": "yes"},
                config_schema=_TONE,
            ),
            "reviewer": _agent("review"),
        },
    )
    AgentService(TEST_DATABASE_URL, "ws_bf").save_draft("drafty", _agent("drafted"), "test")
    WorkflowRevisionService(queries).publish_workspace_revision(
        "ws_bf", workflow_definition_from_yaml_string(_ACTIVE_YAML)
    )
    queries.upsert_workspace_workflow_draft("ws_bf", _DRAFT_YAML)
    queries.upsert_workspace_workflow_draft("ws_broken", "nodes: [unclosed")
    reset_published_agent_cache()
    return queries


def _fingerprint() -> dict[str, str]:
    """md5 of every table's full contents in the test schema."""
    from server.app.db.transaction import read_connection

    with read_connection(TEST_DATABASE_URL) as conn:
        tables = [
            row["table_name"]
            for row in conn.execute(
                "select table_name from information_schema.tables"
                " where table_schema=current_schema() and table_type='BASE TABLE'"
                " order by table_name"
            ).fetchall()
        ]
        fingerprint: dict[str, str] = {}
        for table in tables:
            row = conn.execute(
                "select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) as h"
                f' from "{table}" t'
            ).fetchone()
            assert row is not None
            fingerprint[table] = str(row["h"])
        return fingerprint


def _report() -> dict:
    return build_agent_backfill_report(agent_backfill_reader_from_dsn(TEST_DATABASE_URL))


def test_report_pins_backfill_shared_unresolved_and_overrides(fixture_db: JobQueries) -> None:
    report = _report()

    assert report["summary"] == {
        "workspaces": 3,
        "source_errors": 1,
        "agent_nodes_backfilled": 5,
        "agent_nodes_unresolved": 2,
        "shared_definition_groups": 1,
        "config_schema_overrides": 1,
    }
    assert report["shared_definitions"] == [
        {
            "workspace_id": "ws_bf",
            "source": "active_revision",
            "agent_id": "writer",
            "node_keys": ["draft_a", "draft_b"],
        }
    ]
    assert report["unresolved"] == [
        {
            "workspace_id": "ws_bf",
            "source": "active_revision",
            "node_key": "retired",
            "capability": "legacy",
            "reason": "archived",
            "agent_ids": ["old"],
        },
        {
            "workspace_id": "ws_bf",
            "source": "draft",
            "node_key": "fresh",
            "capability": "drafted",
            "reason": "draft_only",
            "agent_ids": ["drafty"],
        },
    ]
    [override] = report["config_schema_overrides"]
    assert (override["source"], override["node_key"], override["agent_id"]) == (
        "draft",
        "review",
        "reviewer",
    )
    assert override["properties_removed"] == ["strict"]
    assert override["backfilled"] == {}

    ws_bf, ws_broken, ws_empty = report["workspaces"]
    assert ws_bf["published_agents"] == ["reviewer", "writer"]
    active, draft = ws_bf["sources"]
    assert [n["node_key"] for n in active["nodes"]] == ["draft_a", "draft_b", "retired", "review"]
    draft_a = active["nodes"][0]
    assert {k: draft_a[k] for k in ("runtime", "tools", "config_schema", "skill")} == {
        "runtime": "pi",
        "tools": {"value": ["read"], "source": "definition"},
        "config_schema": _TONE,
        "skill": {"key": "grp/writer", "ref": "latest", "source": "definition"},
    }
    assert draft_a["requires_labels"] == {"gpu": "yes"}
    assert active["nodes"][1]["tools"] == {"value": ["bash"], "source": "node"}
    assert active["nodes"][3]["skill"] == {"key": "grp/review", "ref": "v2", "source": "node"}
    assert draft["error"] is None
    assert ws_broken["sources"][0]["source"] == "draft"
    assert ws_broken["sources"][0]["error"]
    assert ws_empty["sources"] == []


def test_report_is_byte_deterministic(fixture_db: JobQueries) -> None:
    first = render_report_json(_report())
    reset_published_agent_cache()

    assert render_report_json(_report()) == first
    assert json.loads(first)["report"] == "agent-definition-backfill-dry-run"


def test_workspace_filter_narrows_the_walk(fixture_db: JobQueries) -> None:
    report = build_agent_backfill_report(
        agent_backfill_reader_from_dsn(TEST_DATABASE_URL), ["ws_empty", "nope"]
    )

    assert [w["workspace_id"] for w in report["workspaces"]] == ["ws_empty"]


def test_dry_run_writes_nothing_to_db_or_object_storage(
    fixture_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_storage(*_args, **_kwargs):
        raise AssertionError("dry-run must not touch object storage")

    import boto3

    monkeypatch.setattr(boto3, "client", _no_storage)
    monkeypatch.setattr(boto3, "resource", _no_storage)
    monkeypatch.setattr(boto3.session.Session, "client", _no_storage)
    before = _fingerprint()
    output = tmp_path / "report.json"

    assert dry_run_main(["--database-url", TEST_DATABASE_URL, "--output", str(output)]) == 0

    assert _fingerprint() == before
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["workspaces"] == 3


def test_reader_connections_reject_writes(fixture_db: JobQueries) -> None:
    import psycopg

    reader = agent_backfill_reader_from_dsn(TEST_DATABASE_URL)

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        reader.upsert_workspace_workflow_draft("ws_empty", "key: ws_empty\n")
    assert fixture_db.get_workspace_workflow_draft("ws_empty") is None


def test_read_only_dsn_keeps_existing_options() -> None:
    from urllib.parse import parse_qs, urlsplit

    [options] = parse_qs(urlsplit(read_only_dsn(TEST_DATABASE_URL)).query)["options"]

    assert "-c default_transaction_read_only=on" in options
    assert "search_path=" in options
