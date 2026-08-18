"""Unit tests for scripts/seed/seed_common.py (pure, no DB)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.seed.seed_common import (  # noqa: E402
    code_violations,
    content_equal,
    secret_like_fields,
    sha256_text,
    validate_seed,
)

pytestmark = pytest.mark.no_db

# Deliberately non-business keys: the tool must stay deployment-agnostic.
WORKFLOW_KEY = "invoices_pipeline"
CODE = "def run(ctx):\n    return None\n"
AGENT_DEFINITION = {
    "capability": "summarize_invoices",
    "runtime": "velites",
    "skill": "acme/summarize",
    "tools": ["read", "write"],
    "requires_labels": {},
    "config_schema": {},
}


def make_definition() -> dict:
    return {
        "key": WORKFLOW_KEY,
        "label": "Invoices",
        "schema_version": 1,
        "intake": {"modes": {"by_id": {"label": "By ID", "input_field": "invoice_ids"}}},
        "nodes": {
            "fetch": {
                "label": "Fetch",
                "capability": "fetch_invoices",
                "after": [],
                "inputs": [],
                "outputs": ["invoices.json"],
            },
            "summarize": {
                "label": "Summarize",
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
                "definition": dict(AGENT_DEFINITION),
                "source_workspace": "acme",
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
            "lock": {"skills": {"acme/summarize": {"commit": "a" * 40}}},
        },
    }


class TestValidateSeed:
    def test_valid_generic_seed_passes(self):
        assert validate_seed(make_seed()) == []

    def test_legacy_executors_key_is_tolerated(self):
        seed = make_seed()
        seed["executors"] = [{"executor_id": "code-default", "definition": {}}]
        assert validate_seed(seed) == []

    def test_rejects_missing_workflow_definition(self):
        seed = make_seed()
        seed["workflows"][0]["definition"] = None
        problems = validate_seed(seed)
        assert any("definition.nodes" in problem for problem in problems)

    def test_rejects_unreferenced_agent_capability(self):
        seed = make_seed()
        seed["agents"][0]["definition"]["capability"] = "orphan_capability"
        problems = validate_seed(seed)
        assert any("not referenced by any exported workflow" in p for p in problems)

    def test_rejects_node_capability_mismatch(self):
        seed = make_seed()
        seed["node_codes"][0]["node_key"] = "summarize"
        problems = validate_seed(seed)
        assert any("belongs to node 'fetch'" in problem for problem in problems)

    def test_rejects_unknown_workflow_for_node_code(self):
        seed = make_seed()
        seed["node_codes"][0]["workflow_key"] = "other_pipeline"
        problems = validate_seed(seed)
        assert any("workflow not present in seed" in problem for problem in problems)

    def test_rejects_code_sha_mismatch(self):
        seed = make_seed()
        seed["node_codes"][0]["code_sha256"] = "0" * 64
        problems = validate_seed(seed)
        assert any("code_sha256 mismatch" in problem for problem in problems)

    def test_rejects_platform_import_by_default(self):
        seed = make_seed()
        code = "import server.app.settings\n\ndef run(ctx):\n    return None\n"
        seed["node_codes"][0]["code"] = code
        seed["node_codes"][0]["code_sha256"] = sha256_text(code)
        problems = validate_seed(seed)
        assert any("forbidden import: server.app.settings" in p for p in problems)

    def test_forbidden_prefixes_are_configurable(self):
        code = "import workspace_libs.legacy_pack\n\ndef run(ctx):\n    return None\n"
        assert code_violations(code) == []
        problems = code_violations(code, ("server.app", "workspace_libs.legacy_pack"))
        assert any("forbidden import" in problem for problem in problems)

    def test_rejects_missing_run_function(self):
        problems = code_violations("VALUE = 1\n")
        assert any("missing module-level 'run' function" in p for p in problems)

    def test_rejects_oversized_code(self):
        code = "def run(ctx):\n    return '" + "x" * 70000 + "'\n"
        problems = code_violations(code)
        assert any("size limit" in problem for problem in problems)

    def test_rejects_skill_lock_source_mismatch(self):
        seed = make_seed()
        seed["skills"]["lock"]["skills"]["acme/other"] = {"commit": "b" * 40}
        problems = validate_seed(seed)
        assert any("sources keys differ" in problem for problem in problems)

    def test_rejects_bad_lock_commit(self):
        seed = make_seed()
        seed["skills"]["lock"]["skills"]["acme/summarize"] = {"commit": "notasha"}
        problems = validate_seed(seed)
        assert any("40-hex sha" in problem for problem in problems)

    def test_rejects_secret_like_literal(self):
        seed = make_seed()
        seed["agents"][0]["definition"]["api_token"] = "abc123"
        problems = validate_seed(seed)
        assert any("secret material" in problem for problem in problems)

    def test_secret_scan_ignores_empty_and_non_string_values(self):
        assert secret_like_fields({"token": "", "password": None, "nested": {"api_key": 0}}) == []


class TestContentEqual:
    def test_key_order_insensitive(self):
        assert content_equal({"a": 1, "b": [1, 2]}, {"b": [1, 2], "a": 1})

    def test_list_order_sensitive(self):
        assert not content_equal({"a": [1, 2]}, {"a": [2, 1]})
