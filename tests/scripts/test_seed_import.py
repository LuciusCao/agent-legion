"""Unit tests for scripts/seed/import_seed.py (in-memory fake instance, no DB/HTTP)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.seed.import_seed import (  # noqa: E402
    parse_workspace_spec,
    step1_workspaces,
    step2_agents,
    step3_first_revisions,
    step4_node_codes,
    step5_skills,
    verify,
)
from scripts.seed.seed_common import sha256_text  # noqa: E402

pytestmark = pytest.mark.no_db

WORKFLOW_KEY = "invoices_pipeline"
WORKSPACE_ID = "acme"
CODE = "def run(ctx):\n    return None\n"
SKILL_COMMIT = "a" * 40


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


def make_seed() -> dict:
    return {
        "schema_version": 1,
        "workflows": [
            {
                "key": WORKFLOW_KEY,
                "label": "Invoices",
                "description": "",
                "origin": "registered",
                "definition": make_definition(),
            }
        ],
        "agents": [
            {
                "agent_id": "invoice-summarizer-v1",
                "capability": "summarize_invoices",
                "definition": {
                    "capability": "summarize_invoices",
                    "runtime": "velites",
                    "skill": "acme/summarize",
                    "tools": ["read", "write"],
                    "requires_labels": {},
                    "config_schema": {},
                },
                "source_workspace": WORKSPACE_ID,
                "source_version": 1,
            }
        ],
        "node_codes": [
            {
                "workflow_key": WORKFLOW_KEY,
                "node_key": "fetch",
                "capability": "fetch_invoices",
                "code": CODE,
                "code_sha256": sha256_text(CODE),
                "change_note": "seed export",
            }
        ],
        "skills": {
            "sources": {"acme/summarize": {"repo": "/opt/acme/skills", "ref": "v1.0.0"}},
            "lock": {"skills": {"acme/summarize": {"commit": SKILL_COMMIT}}},
        },
    }


def _api_payload(definition: dict) -> dict:
    return {
        "nodes": [
            {
                "key": key,
                "capability": node.get("capability"),
                "inputs": node.get("inputs") or [],
                "outputs": node.get("outputs") or [],
                "after": node.get("after") or [],
                "terminal": node.get("terminal"),
            }
            for key, node in (definition.get("nodes") or {}).items()
        ],
        "edges": [
            {"source": e.get("from"), "target": e.get("to"), "condition": e.get("when")}
            for e in (definition.get("edges") or [])
        ],
    }


class FakeClient:
    """In-memory simulation of the target instance's HTTP API surface."""

    def __init__(self, dry_run: bool = False, with_revision: bool = True) -> None:
        self.dry_run = dry_run
        self.actions: list[str] = []
        self.workspaces: dict[str, dict] = {
            WORKSPACE_ID: {
                "id": WORKSPACE_ID,
                "name": "Acme",
                "default_workflow_key": WORKFLOW_KEY,
                "default_entity": "invoice",
            }
        }
        self.revisions: dict[str, dict] = {WORKSPACE_ID: make_definition()} if with_revision else {}
        self.agents: dict[tuple[str, str], dict] = {}
        self.node_codes: dict[tuple[str, str, str], dict] = {}
        self.skills: dict[str, dict] = {}

    # -- reads -------------------------------------------------------------

    def _missing(self, path: str, allow_404: bool) -> None:
        if allow_404:
            return None
        raise AssertionError(f"unexpected 404: GET {path}")

    def get(self, path: str, *, allow_404: bool = False, params: dict | None = None) -> Any:
        if path == "/api/workspaces":
            return {"workspaces": list(self.workspaces.values())}
        if path.endswith("/workflow-revisions/active"):
            workspace_id = path.split("/")[3]
            if workspace_id not in self.revisions:
                return self._missing(path, allow_404)
            return {
                "revision": {"version": 1},
                "workflow": _api_payload(self.revisions[workspace_id]),
            }
        if path.startswith("/api/agent-definitions/"):
            agent_id = path.rsplit("/", 1)[1]
            state = self.agents.get(((params or {}).get("workspace_id"), agent_id))
            if state is None:
                return self._missing(path, allow_404)
            return {
                "latest": state.get("draft") or state.get("published"),
                "published": state.get("published"),
            }
        if "/nodes/" in path and path.endswith("/code"):
            parts = path.split("/")
            workspace_id, workflow_key, node_key = parts[3], parts[5], parts[7]
            revision = self.revisions.get(workspace_id)
            if revision is None or node_key not in (revision.get("nodes") or {}):
                return self._missing(path, allow_404)
            state = self.node_codes.get((workspace_id, workflow_key, node_key)) or {}
            published = state.get("published")
            if published is None:
                return {"origin": "none", "code": "", "has_draft": False}
            return {
                "origin": "custom",
                "code": published["code"],
                "version": published["version"],
                "has_draft": False,
            }
        if path == "/api/admin/skill-sources":
            return {"skills": [{"key": key, **value} for key, value in sorted(self.skills.items())]}
        raise AssertionError(f"unexpected GET {path}")

    # -- writes ------------------------------------------------------------

    def mutate(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        params: dict | None = None,
        expect: tuple[int, ...] = (200,),
        timeout: int = 30,
        dry_note: str | None = None,
    ) -> Any:
        prefix = "WOULD" if self.dry_run else "DID  "
        self.actions.append(f"{prefix} {method} {path}" + (f" — {dry_note}" if dry_note else ""))
        if self.dry_run:
            return None
        return self._apply(method, path, body, params)

    def _apply(self, method: str, path: str, body: dict | None, params: dict | None) -> Any:
        body = body or {}
        if method == "POST" and path == "/api/workspaces":
            workspace_id = body["name"].lower().replace(" ", "_")
            self.workspaces[workspace_id] = {"id": workspace_id, **body}
            return {"workspace": self.workspaces[workspace_id]}
        if method == "POST" and path.endswith("/workflow-drafts/publish"):
            workspace_id = path.split("/")[3]
            definition = _definition_from_yaml(body["definition_yaml"])
            errors = []
            for node_key, node in (definition.get("nodes") or {}).items():
                capability = node.get("capability")
                routed = any(
                    (state.get("published") or {}).get("definition", {}).get("capability")
                    == capability
                    for (ws, _), state in self.agents.items()
                    if ws == workspace_id
                )
                coded = (self.node_codes.get((workspace_id, WORKFLOW_KEY, node_key)) or {}).get(
                    "published"
                )
                if not routed and not coded:
                    errors.append(f"no published node code for {WORKFLOW_KEY}.{node_key}")
            if errors:
                return {"valid": False, "errors": errors}
            self.revisions[workspace_id] = definition
            return {"valid": True, "errors": []}
        if method == "POST" and path == "/api/agent-definitions":
            workspace_id = (params or {})["workspace_id"]
            definition = {k: v for k, v in body.items() if k != "agent_id"}
            self.agents[(workspace_id, body["agent_id"])] = {"draft": definition, "published": None}
            return {"version": 1, "status": "draft"}
        if method == "PUT" and path.endswith("/draft") and "/agent-definitions/" in path:
            agent_id = path.split("/")[3]
            workspace_id = (params or {})["workspace_id"]
            self.agents.setdefault((workspace_id, agent_id), {"published": None})
            self.agents[(workspace_id, agent_id)]["draft"] = body
            return {"status": "draft"}
        if method == "POST" and path.endswith("/publish") and "/agent-definitions/" in path:
            agent_id = path.split("/")[3]
            workspace_id = (params or {})["workspace_id"]
            state = self.agents[(workspace_id, agent_id)]
            version = ((state.get("published") or {}).get("version") or 0) + 1
            state["published"] = {"version": version, "definition": state["draft"]}
            return {"version": version}
        if method == "PUT" and path.endswith("/code"):
            key = (path.split("/")[3], path.split("/")[5], path.split("/")[7])
            state = self.node_codes.setdefault(key, {"published": None})
            state["draft"] = body["code"]
            return {"status": "draft"}
        if method == "POST" and path.endswith("/code/publish"):
            key = (path.split("/")[3], path.split("/")[5], path.split("/")[7])
            state = self.node_codes[key]
            version = ((state.get("published") or {}).get("version") or 0) + 1
            state["published"] = {"version": version, "code": state["draft"]}
            return {"version": version}
        if method == "PUT" and path.startswith("/api/admin/skill-sources/"):
            skill_key = path[len("/api/admin/skill-sources/") :]
            self.skills[skill_key] = {
                "repo": body["repo"],
                "ref": body["ref"],
                "locked_commit": None,
                "resolved_at": None,
                "stale": True,
            }
            return {"skills": []}
        if method == "POST" and path == "/api/admin/skill-sources/relock":
            for value in self.skills.values():
                value["locked_commit"] = SKILL_COMMIT
                value["stale"] = False
            return {"skills": []}
        raise AssertionError(f"unexpected {method} {path}")


def _definition_from_yaml(text: str) -> dict:
    import yaml

    return yaml.safe_load(text)


def run_all(client: FakeClient, seed: dict, specs: list[str]) -> list[str]:
    failures: list[str] = []
    bound = step1_workspaces(client, seed, specs, failures)
    step2_agents(client, seed, bound, failures)
    step3_first_revisions(client, seed, bound, failures)
    step4_node_codes(client, seed, bound, failures)
    step5_skills(client, seed, failures)
    return failures + verify(client, seed, bound)


def test_first_import_writes_and_verifies():
    client = FakeClient()
    failures = run_all(client, make_seed(), [])
    assert failures == []
    assert client.agents[(WORKSPACE_ID, "invoice-summarizer-v1")]["published"]["version"] == 1
    assert client.node_codes[(WORKSPACE_ID, WORKFLOW_KEY, "fetch")]["published"]["code"] == CODE
    assert client.skills["acme/summarize"]["locked_commit"] == SKILL_COMMIT


def test_second_run_is_fully_idempotent():
    client = FakeClient()
    assert run_all(client, make_seed(), []) == []
    client.actions.clear()
    assert run_all(client, make_seed(), []) == []
    writes = [a for a in client.actions if a.startswith("DID")]
    assert writes == []


def test_content_guard_republishes_only_drifted_agent():
    client = FakeClient()
    assert run_all(client, make_seed(), []) == []
    # Simulate an admin editing the Agent definition on the target instance.
    state = client.agents[(WORKSPACE_ID, "invoice-summarizer-v1")]
    edited = dict(state["published"]["definition"])
    edited["skill"] = "acme/summarize-v2"
    state["published"]["definition"] = edited

    client.actions.clear()
    assert run_all(client, make_seed(), []) == []
    writes = [a for a in client.actions if a.startswith("DID")]
    assert len(writes) == 2  # save draft + publish, for the drifted Agent only
    assert all("agent-definitions" in action for action in writes)
    assert state["published"]["version"] == 2
    assert state["published"]["definition"]["skill"] == "acme/summarize"


def test_node_code_drift_republishes_only_that_node():
    client = FakeClient()
    assert run_all(client, make_seed(), []) == []
    key = (WORKSPACE_ID, WORKFLOW_KEY, "fetch")
    client.node_codes[key]["published"]["code"] = CODE + "\n# local edit\n"

    client.actions.clear()
    assert run_all(client, make_seed(), []) == []
    writes = [a for a in client.actions if a.startswith("DID")]
    assert len(writes) == 2  # save draft + publish, for the drifted node only
    assert all("/nodes/fetch/code" in action for action in writes)
    assert client.node_codes[key]["published"]["version"] == 2


def test_dry_run_writes_nothing():
    client = FakeClient(dry_run=True)
    run_all(client, make_seed(), [])
    assert client.agents == {}
    assert client.node_codes == {}
    assert client.skills == {}
    assert client.actions
    assert all(action.startswith("WOULD") for action in client.actions)


def test_fresh_workspace_bootstrap_gap_is_reported():
    """Known platform gap: first revision publish requires published node
    code, while node code publish requires an active revision. The tool must
    surface this as a clear failure, not hang or half-apply."""
    client = FakeClient(with_revision=False)
    client.workspaces = {}
    failures = run_all(client, make_seed(), ["Acme=invoices_pipeline:invoice"])
    assert any("no published node code" in failure for failure in failures)
    assert any("draft publish failed" in failure for failure in failures)


def test_workspace_spec_creation_flow():
    client = FakeClient(with_revision=True)
    # Unbind the pre-seeded workspace so the spec drives creation.
    client.workspaces = {}
    client.revisions = {}
    run_all(client, make_seed(), ["Acme Labs=invoices_pipeline:invoice"])
    assert "acme_labs" in client.workspaces
    assert client.workspaces["acme_labs"]["default_entity"] == "invoice"


def test_parse_workspace_spec():
    assert parse_workspace_spec("Acme=invoices_pipeline:invoice") == (
        "Acme",
        "invoices_pipeline",
        "invoice",
    )
    assert parse_workspace_spec("Acme=invoices_pipeline") == ("Acme", "invoices_pipeline", None)
    with pytest.raises(SystemExit):
        parse_workspace_spec("no-separator")
