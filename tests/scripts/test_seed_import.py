"""Unit tests for scripts/seed/import_seed.py (in-memory fake instance, no DB/HTTP)."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.seed.import_seed import (  # noqa: E402
    parse_workspace_spec,
    step1_workspaces,
    step2_first_revisions,
    step3_node_codes,
    step4_skills,
    verify,
)
from scripts.seed.seed_common import sha256_text  # noqa: E402
from server.app.services.workflow_revision_format import definition_to_yaml  # noqa: E402
from server.app.workflows.definition import workflow_definition_from_dict  # noqa: E402

pytestmark = pytest.mark.no_db

WORKFLOW_KEY = "invoices_pipeline"
# The workspace id is the workflow key (schema v62 / #211 M3).
WORKSPACE_ID = WORKFLOW_KEY
CODE = "def run(ctx):\n    return None\n"
SKILL_COMMIT = "a" * 40


def make_definition() -> dict:
    return {
        "key": WORKFLOW_KEY,
        "label": "Invoices",
        "nodes": {
            "fetch": {
                "type": "code",
                "capability": "fetch_invoices",
                "after": [],
                "inputs": [],
                "outputs": ["invoices.json"],
            },
            "summarize": {
                "type": "agent",
                "capability": "summarize_invoices",
                "after": ["fetch"],
                "inputs": ["invoices.json"],
                "outputs": ["summary.md"],
            },
        },
        "edges": [{"from": "fetch", "to": "summarize"}],
    }


def make_agent_definition() -> dict:
    return {
        "capability": "summarize_invoices",
        "runtime": "velites",
        "skill": "acme/summarize",
        "tools": ["read", "write"],
        "requires_labels": {"region": "cn"},
        "config_schema": {
            "type": "object",
            "properties": {"locale": {"type": "string", "default": "zh-CN"}},
        },
    }


def make_self_contained_definition() -> dict:
    definition = make_definition()
    agent = make_agent_definition()
    definition["nodes"]["summarize"].update(
        execution={"runtime": agent["runtime"]},
        skill={"key": agent["skill"], "ref": "latest"},
        tools=agent["tools"],
        requires_labels=agent["requires_labels"],
        config_schema=agent["config_schema"],
    )
    return definition


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
                "definition": make_agent_definition(),
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
            # #322: lock only (the source registry is retired); step 4 is advisory.
            "lock": {
                "skills": {
                    "acme/summarize": {"repo": "/opt/acme/skills", "refs": {"v1.0.0": SKILL_COMMIT}}
                }
            },
        },
    }


def _api_payload(definition: dict) -> dict:
    return {
        "nodes": [
            {
                "key": key,
                "node_type": node.get("type", "code"),
                "capability": node.get("capability"),
                "inputs": node.get("inputs") or [],
                "outputs": node.get("outputs") or [],
                "after": node.get("after") or [],
                "execution": node.get("execution") or {},
                "skill": node.get("skill"),
                "tools": node.get("tools") or [],
                "requires_labels": node.get("requires_labels") or {},
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
                "default_entity": "invoice",
            }
        }
        self.revisions: dict[str, dict] = (
            {WORKSPACE_ID: make_self_contained_definition()} if with_revision else {}
        )
        # Historical rows are plain fixture state: any Agent API access fails.
        self.agents: dict[tuple[str, str], dict] = {}
        self.node_codes: dict[tuple[str, str, str], dict] = {}

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
            definition = self.revisions[workspace_id]
            return {
                "revision": {"version": 1},
                "workflow": _api_payload(definition),
                "definition_yaml": definition_to_yaml(workflow_definition_from_dict(definition)),
            }
        if path.startswith("/api/agent-definitions"):
            raise AssertionError(f"import must not read the Agent catalog: GET {path}")
        if "/nodes/" in path and path.endswith("/code"):
            workspace_id, workflow_key, node_key = _node_code_key(path)
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
        if "/agent-definitions" in path:
            raise AssertionError(f"import must not write the Agent catalog: {method} {path}")
        if method == "POST" and path == "/api/workspaces":
            workspace_id = body["id"]
            self.workspaces[workspace_id] = {"id": workspace_id, **body}
            return {"workspace": self.workspaces[workspace_id]}
        if method == "POST" and path.endswith("/workflow-drafts/publish"):
            workspace_id = path.split("/")[3]
            definition = _definition_from_yaml(body["definition_yaml"])
            errors = []
            for node_key, node in (definition.get("nodes") or {}).items():
                if node.get("type") == "agent":
                    if not (node.get("execution") or {}).get("runtime"):
                        errors.append(f"agent node {node_key} has no execution.runtime")
                    if not node.get("skill"):
                        errors.append(f"agent node {node_key} has no skill")
                    continue
                coded = (self.node_codes.get((workspace_id, WORKFLOW_KEY, node_key)) or {}).get(
                    "published"
                )
                if not coded:
                    errors.append(f"no published node code for {WORKFLOW_KEY}.{node_key}")
            if errors:
                return {"valid": False, "errors": errors}
            self.revisions[workspace_id] = definition
            return {"valid": True, "errors": []}
        if method == "PUT" and path.endswith("/code"):
            key = _node_code_key(path)
            state = self.node_codes.setdefault(key, {"published": None})
            state["draft"] = body["code"]
            return {"status": "draft", "code_hash": _fake_hash(body["code"])}
        if method == "POST" and path.endswith("/code/publish"):
            key = _node_code_key(path)
            state = self.node_codes[key]
            assert body == {"expected_hash": _fake_hash(state["draft"])}
            version = ((state.get("published") or {}).get("version") or 0) + 1
            state["published"] = {"version": version, "code": state["draft"]}
            return {"version": version}
        raise AssertionError(f"unexpected {method} {path}")


def _node_code_key(path: str) -> tuple[str, str, str]:
    """(workspace, workflow key, node) from the live node-code route.

    #211 M3 removed the /workflows/{workflow_key}/ alias (the real Host
    404s on it), so the fake rejects it instead of silently serving it."""
    parts = path.split("/")
    assert "workflows" not in parts, f"retired workflows/{{key}} alias: {path}"
    assert parts[1:3] == ["api", "workspaces"] and parts[4] == "nodes", path
    assert parts[6] == "code", path
    return parts[3], parts[3], parts[5]


def _fake_hash(content: object) -> str:
    return f"h:{json.dumps(content, sort_keys=True)}"


def _definition_from_yaml(text: str) -> dict:
    import yaml

    return yaml.safe_load(text)


def run_all(client: FakeClient, seed: dict, specs: list[str]) -> list[str]:
    failures: list[str] = []
    bound = step1_workspaces(client, seed, specs, failures)
    step2_first_revisions(client, seed, bound, failures)
    step3_node_codes(client, seed, bound, failures)
    step4_skills(client, seed, failures)
    return failures + verify(client, seed, bound)


def test_first_import_writes_and_verifies():
    client = FakeClient()
    failures = run_all(client, make_seed(), [])
    assert failures == []
    assert client.agents == {}
    assert client.node_codes[(WORKSPACE_ID, WORKFLOW_KEY, "fetch")]["published"]["code"] == CODE
    assert not any("agent-definitions" in action for action in client.actions)


def test_first_revision_only_inlines_legacy_agents():
    client = FakeClient(with_revision=False)
    seed = make_seed()
    definition = make_definition()
    summarize = definition["nodes"].pop("summarize")
    summarize.update(after=[], inputs=[])
    seed["workflows"][0]["definition"] = {
        "key": WORKFLOW_KEY,
        "label": "Invoices",
        "nodes": {"summarize": summarize},
        "edges": [],
    }
    seed["node_codes"] = []

    assert run_all(client, seed, []) == []

    agent = make_agent_definition()
    node = client.revisions[WORKSPACE_ID]["nodes"]["summarize"]
    assert node["execution"] == {"runtime": agent["runtime"]}
    assert node["skill"] == {"key": agent["skill"], "ref": "latest"}
    assert node["tools"] == agent["tools"]
    assert node["requires_labels"] == agent["requires_labels"]
    assert node["config_schema"] == agent["config_schema"]
    assert client.agents == {}
    assert not any("agent-definitions" in action for action in client.actions)


def test_import_tolerates_v1_lock_shape(capsys):
    """#322: step 4 is advisory only — a legacy v1 lock entry ({repo, ref,
    commit}) is reported through the same lock_entry_refs upgrade, never
    written back over deleted admin endpoints."""
    seed = make_seed()
    seed["skills"]["lock"]["skills"]["acme/summarize"] = {
        "repo": "/opt/acme/skills",
        "ref": "v1.0.0",
        "commit": SKILL_COMMIT,
    }
    client = FakeClient()
    assert run_all(client, seed, []) == []
    assert "acme/summarize" in capsys.readouterr().out


def test_second_run_is_fully_idempotent():
    client = FakeClient()
    assert run_all(client, make_seed(), []) == []
    client.actions.clear()
    assert run_all(client, make_seed(), []) == []
    writes = [a for a in client.actions if a.startswith("DID")]
    assert writes == []


def test_import_preserves_agent_history_without_catalog_api_calls():
    client = FakeClient()
    client.agents[(WORKSPACE_ID, "invoice-summarizer-v1")] = {
        "published": {"version": 7, "definition": make_agent_definition()}
    }
    history_before = copy.deepcopy(client.agents)

    assert run_all(client, make_seed(), []) == []

    assert client.agents == history_before
    assert not any("agent-definitions" in action for action in client.actions)


def test_verify_rejects_node_profile_drift():
    client = FakeClient()
    client.revisions[WORKSPACE_ID]["nodes"]["summarize"]["config_schema"] = {}

    failures = run_all(client, make_seed(), [])

    assert any("agent profiles" in failure and "matches=False" in failure for failure in failures)


def test_verify_rejects_an_incomplete_node_profile():
    client = FakeClient()
    client.revisions[WORKSPACE_ID]["nodes"]["summarize"].pop("skill")
    seed = make_seed()
    seed["agents"][0]["definition"]["skill"] = ""

    failures = run_all(client, seed, [])

    assert any("incomplete=['summarize']" in failure for failure in failures)


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
    assert WORKFLOW_KEY in client.workspaces
    assert client.workspaces[WORKFLOW_KEY]["name"] == "Acme Labs"
    assert client.workspaces[WORKFLOW_KEY]["default_entity"] == "invoice"


def test_parse_workspace_spec():
    assert parse_workspace_spec("Acme=invoices_pipeline:invoice") == (
        "Acme",
        "invoices_pipeline",
        "invoice",
    )
    assert parse_workspace_spec("Acme=invoices_pipeline") == ("Acme", "invoices_pipeline", None)
    with pytest.raises(SystemExit):
        parse_workspace_spec("no-separator")
