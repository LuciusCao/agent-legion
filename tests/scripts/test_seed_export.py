"""Unit tests for scripts/seed/export_seed.py (fake connection, no DB)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.seed.export_seed import (  # noqa: E402
    build_seed,
    export_agents,
    export_node_codes,
    parse_node_code_override,
    resolve_source_workspaces,
)
from scripts.seed.seed_common import validate_seed  # noqa: E402

pytestmark = pytest.mark.no_db

WORKFLOW_KEY = "invoices_pipeline"


def make_definition() -> dict:
    return {
        "key": WORKFLOW_KEY,
        "label": "Invoices",
        "nodes": {
            "fetch": {
                "capability": "fetch_invoices",
                "after": [],
                "inputs": [],
                "outputs": ["invoices.json"],
            },
            "summarize": {
                "capability": "summarize_invoices",
                "after": ["fetch"],
                "inputs": ["invoices.json"],
                "outputs": ["summary.md"],
            },
        },
        "edges": [{"from": "fetch", "to": "summarize"}],
    }


def agent_definition(capability: str = "summarize_invoices", skill: str = "acme/summarize") -> dict:
    return {
        "capability": capability,
        "runtime": "velites",
        "skill": skill,
        "tools": ["read", "write"],
        "requires_labels": {},
        "config_schema": {},
    }


class FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class FakeConn:
    """Duck-typed psycopg connection over canned rows (dict_row style)."""

    def __init__(
        self,
        *,
        catalog_rows: list[dict] | None = None,
        workspace_rows: list[dict] | None = None,
        agent_rows: list[dict] | None = None,
        node_code_rows: list[dict] | None = None,
        settings_rows: list[dict] | None = None,
    ) -> None:
        self.catalog_rows = catalog_rows or []
        self.workspace_rows = workspace_rows or []
        self.agent_rows = agent_rows or []
        self.node_code_rows = node_code_rows or []
        self.settings_rows = settings_rows or []

    def execute(self, sql: str, params: tuple | None = None) -> FakeResult:
        if "from workflow_catalog" in sql:
            keys = set(params[0]) if params else set()
            return FakeResult([r for r in self.catalog_rows if r["key"] in keys])
        if "from workspaces" in sql:
            wanted = set(params[0]) if params else set()
            rows = [
                r
                for r in self.workspace_rows
                if r.get("id") in wanted or r.get("default_workflow_key") in wanted
            ]
            return FakeResult(rows)
        if "entity_type='agent'" in sql:
            wanted = set(params[0]) if params else set()
            return FakeResult([r for r in self.agent_rows if r["workspace_id"] in wanted])
        if "entity_type='node_code'" in sql and "is null" in sql:
            return FakeResult([r for r in self.node_code_rows if r["workspace_id"] is None])
        if "entity_type='node_code'" in sql:
            wanted = set(params[0]) if params else set()
            return FakeResult([r for r in self.node_code_rows if r["workspace_id"] in wanted])
        if "from global_settings" in sql:
            return FakeResult(list(self.settings_rows))
        raise AssertionError(f"unexpected SQL: {sql}")


def catalog_row(key: str = WORKFLOW_KEY) -> dict:
    return {
        "key": key,
        "label": "Invoices",
        "description": "",
        "origin": "registered",
        "definition_json": json.dumps(make_definition()),
    }


def agent_row(
    workspace: str,
    agent_id: str,
    capability: str = "summarize_invoices",
    skill: str = "acme/summarize",
) -> dict:
    return {
        "workspace_id": workspace,
        "entity_key": agent_id,
        "version": 1,
        "definition_json": json.dumps(agent_definition(capability, skill)),
    }


def node_code_row(workspace: str | None, entity_key: str, code: str, version: int = 1) -> dict:
    return {
        "workspace_id": workspace,
        "entity_key": entity_key,
        "version": version,
        "definition_json": json.dumps({"code": code}),
    }


CODE = "def run(ctx):\n    return None\n"


def test_build_seed_assembles_generic_package():
    conn = FakeConn(
        catalog_rows=[catalog_row()],
        workspace_rows=[{"id": "acme", "default_workflow_key": WORKFLOW_KEY}],
        agent_rows=[
            agent_row("acme", "invoice-summarizer-v1"),
            # capability not referenced by the exported DAG -> excluded
            agent_row("acme", "unrelated-v1", capability="unrelated_capability"),
        ],
        node_code_rows=[node_code_row("acme", f"{WORKFLOW_KEY}:fetch", CODE)],
        settings_rows=[
            {
                "key": "skill_sources",
                "value": json.dumps({"skills": {"acme/summarize": {"repo": "/r", "ref": "v1"}}}),
            },
            {
                "key": "skill_lock",
                "value": json.dumps({"skills": {"acme/summarize": {"commit": "a" * 40}}}),
            },
        ],
    )
    seed, warnings = build_seed(conn, [WORKFLOW_KEY], None, {})
    assert warnings == []
    assert [w["key"] for w in seed["workflows"]] == [WORKFLOW_KEY]
    assert [a["agent_id"] for a in seed["agents"]] == ["invoice-summarizer-v1"]
    assert seed["agents"][0]["source_workspace"] == "acme"
    assert [n["node_key"] for n in seed["node_codes"]] == ["fetch"]
    assert seed["skills"]["sources"]["acme/summarize"]["ref"] == "v1"
    assert validate_seed(seed) == []


def test_export_warns_on_unknown_workflow():
    conn = FakeConn()
    seed, warnings = build_seed(conn, ["missing_pipeline"], None, {})
    assert seed["workflows"] == []
    assert any("no catalog row" in warning for warning in warnings)


def test_agent_conflict_across_workspaces_is_skipped():
    workflows = [{"key": WORKFLOW_KEY, "definition": make_definition()}]
    conn = FakeConn(
        agent_rows=[
            agent_row("ws_a", "summarizer-a", skill="acme/summarize"),
            agent_row("ws_b", "summarizer-b", skill="acme/other-skill"),
        ]
    )
    agents, warnings = export_agents(conn, workflows, ["ws_a", "ws_b"])
    assert agents == []
    assert any("conflicting published definitions" in w for w in warnings)


def test_agent_identical_across_workspaces_is_deduped():
    workflows = [{"key": WORKFLOW_KEY, "definition": make_definition()}]
    conn = FakeConn(
        agent_rows=[
            agent_row("ws_a", "summarizer-a"),
            agent_row("ws_b", "summarizer-b"),
        ]
    )
    agents, warnings = export_agents(conn, workflows, ["ws_a", "ws_b"])
    assert warnings == []
    assert len(agents) == 1
    assert agents[0]["agent_id"] == "summarizer-a"  # deterministic: sorted by entity_key


def test_node_code_conflict_across_workspaces_is_skipped():
    workflows = [{"key": WORKFLOW_KEY, "definition": make_definition()}]
    conn = FakeConn(
        node_code_rows=[
            node_code_row("ws_a", f"{WORKFLOW_KEY}:fetch", CODE),
            node_code_row("ws_b", f"{WORKFLOW_KEY}:fetch", CODE + "\n# drift\n"),
        ]
    )
    entries, warnings = export_node_codes(conn, workflows, ["ws_a", "ws_b"], {})
    assert entries == []
    assert any("conflicting published code" in w for w in warnings)


def test_node_code_falls_back_to_global_factory_seed():
    workflows = [{"key": WORKFLOW_KEY, "definition": make_definition()}]
    conn = FakeConn(node_code_rows=[node_code_row(None, f"{WORKFLOW_KEY}:fetch", CODE)])
    entries, warnings = export_node_codes(conn, workflows, ["ws_a"], {})
    assert warnings == []
    assert len(entries) == 1
    assert entries[0]["source_workspace"] is None
    # The agent-routed node has no code entry.
    assert all(e["node_key"] != "summarize" for e in entries)


def test_node_code_override_reads_file(tmp_path: Path):
    code_file = tmp_path / "fetch.py"
    override_code = "def run(ctx):\n    return 'from-file'\n"
    code_file.write_text(override_code, encoding="utf-8")
    workflows = [{"key": WORKFLOW_KEY, "definition": make_definition()}]
    conn = FakeConn(node_code_rows=[node_code_row("ws_a", f"{WORKFLOW_KEY}:fetch", CODE)])
    entries, warnings = export_node_codes(
        conn, workflows, ["ws_a"], {"fetch_invoices": str(code_file)}
    )
    assert warnings == []
    by_node = {e["node_key"]: e for e in entries}
    assert by_node["fetch"]["code"] == override_code
    assert by_node["fetch"]["source_file"] == str(code_file)


def test_node_code_override_unknown_capability_warns():
    workflows = [{"key": WORKFLOW_KEY, "definition": make_definition()}]
    conn = FakeConn()
    entries, warnings = export_node_codes(conn, workflows, [], {"ghost": "x.py"})
    assert entries == []
    assert any("not found in exported workflow definitions" in w for w in warnings)


def test_resolve_source_workspaces_explicit_missing_warns():
    conn = FakeConn(workspace_rows=[{"id": "acme", "default_workflow_key": WORKFLOW_KEY}])
    ids, warnings = resolve_source_workspaces(conn, [WORKFLOW_KEY], ["acme", "ghost"])
    assert ids == ["acme"]
    assert any("ghost" in warning for warning in warnings)


def test_parse_node_code_override():
    assert parse_node_code_override("cap=path/to.py") == ("cap", "path/to.py")
    with pytest.raises(SystemExit):
        parse_node_code_override("no-separator")
