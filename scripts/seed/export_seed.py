#!/usr/bin/env python3
"""Export a workflow seed package (seed.json) from a live instance database.

READ-ONLY: only SELECT statements run against the source database. The tool
is generic — it exports whichever workflows ``--workflow`` names, together
with everything those workflows need to run on another instance:

- workflows[]: catalog definitions of the requested workflow keys
- agents[]: published Agent definitions (workspace-scoped since schema v46)
  whose capability is referenced by an exported workflow, collected from the
  source workspaces
- node_codes[]: published custom node code texts for nodes of the exported
  workflows (workspace-scoped versions from the source workspaces, falling
  back to global factory-seeded versions); ``--node-code capability=path``
  overrides any node's code text with a local file instead
- skills{}: the instance's skill_sources / skill_lock documents

Secrets are never exported: definitions carry vault *references* only, and
the final seed is scanned for secret-looking literal values (see
seed_common.SECRET_KEY_PATTERN).

Usage (repo root):

    uv run python -m scripts.seed.export_seed \
        --dsn "$AGENT_LEGION_DATABASE_URL" \
        --workflow my_pipeline --workflow other_pipeline \
        [--workspace ws_id] [--node-code some_capability=path/to/code.py] \
        --output seed.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

from scripts.seed.seed_common import (
    DEFAULT_FORBIDDEN_IMPORT_PREFIXES,
    SEED_SCHEMA_VERSION,
    filter_agent_definition,
    node_key_for_capability,
    sha256_text,
    validate_seed,
    workflow_capabilities,
)

DEFAULT_CHANGE_NOTE = "seed export"


def _masked_dsn(dsn: str) -> str:
    parsed = urlparse(dsn)
    return f"{parsed.hostname}:{parsed.port}{parsed.path}"


def export_workflows(conn: Any, workflow_keys: list[str]) -> tuple[list[dict], list[str]]:
    rows = conn.execute(
        "select key, label, description, origin, definition_json from workflow_catalog "
        "where key = any(%s) order by key",
        (list(workflow_keys),),
    ).fetchall()
    warnings: list[str] = []
    workflows: list[dict] = []
    for row in rows:
        definition = json.loads(row["definition_json"]) if row["definition_json"] else None
        if definition is None:
            warnings.append(f"workflow {row['key']}: catalog row has no definition_json")
        workflows.append(
            {
                "key": row["key"],
                "label": row["label"],
                "description": row["description"] or "",
                "origin": row["origin"],
                "definition": definition,
            }
        )
    found = {w["key"] for w in workflows}
    for key in workflow_keys:
        if key not in found:
            warnings.append(f"workflow {key}: no catalog row in source DB")
    return workflows, warnings


def resolve_source_workspaces(
    conn: Any, workflow_keys: list[str], workspace_ids: list[str] | None
) -> tuple[list[str], list[str]]:
    """Source workspaces for agent/node-code export. Default: every workspace
    bound to one of the exported workflows."""
    if workspace_ids:
        rows = conn.execute(
            "select id from workspaces where id = any(%s) order by id",
            (list(workspace_ids),),
        ).fetchall()
        found = {str(row["id"]) for row in rows}
        warnings = [
            f"workspace {workspace_id}: not present in source DB"
            for workspace_id in workspace_ids
            if workspace_id not in found
        ]
        return sorted(found), warnings
    rows = conn.execute(
        "select id from workspaces where default_workflow_key = any(%s) order by id",
        (list(workflow_keys),),
    ).fetchall()
    return [str(row["id"]) for row in rows], []


def export_agents(
    conn: Any, workflows: list[dict], workspace_ids: list[str]
) -> tuple[list[dict], list[str]]:
    """Published Agent definitions whose capability an exported workflow
    references. Capabilities with conflicting definitions across the source
    workspaces are skipped with a warning (export is per-capability)."""
    capabilities: set[str] = set()
    for workflow in workflows:
        definition = workflow.get("definition")
        if isinstance(definition, dict):
            capabilities |= workflow_capabilities(definition)
    if not capabilities or not workspace_ids:
        return [], []
    rows = conn.execute(
        "select workspace_id, entity_key, version, definition_json from versioned_entities "
        "where entity_type='agent' and status='published' and workspace_id = any(%s) "
        "order by entity_key",
        (list(workspace_ids),),
    ).fetchall()
    warnings: list[str] = []
    by_capability: dict[str, list[dict]] = {}
    for row in rows:
        definition = filter_agent_definition(json.loads(row["definition_json"]))
        capability = str(definition.get("capability") or "")
        if capability not in capabilities:
            continue
        by_capability.setdefault(capability, []).append(
            {
                "agent_id": row["entity_key"],
                "capability": capability,
                "definition": definition,
                "source_workspace": row["workspace_id"],
                "source_version": row["version"],
            }
        )
    agents: list[dict] = []
    for capability in sorted(by_capability):
        candidates = by_capability[capability]
        distinct = {json.dumps(c["definition"], sort_keys=True) for c in candidates}
        if len(distinct) > 1:
            ids = sorted(c["agent_id"] for c in candidates)
            warnings.append(
                f"agent capability {capability}: conflicting published definitions "
                f"across source workspaces ({ids}); skipped — reconcile first"
            )
            continue
        agents.append(candidates[0])
    return agents, warnings


def export_node_codes(
    conn: Any,
    workflows: list[dict],
    workspace_ids: list[str],
    overrides: dict[str, str],
    change_note: str = DEFAULT_CHANGE_NOTE,
) -> tuple[list[dict], list[str]]:
    """Published custom node code for every node of the exported workflows.

    Resolution per node: explicit ``--node-code`` file override > workspace
    published version (must be identical across source workspaces) > global
    factory-seeded version. Nodes without any published code are skipped
    (agent-routed nodes need no code)."""
    warnings: list[str] = []
    entries: list[dict] = []

    workspace_rows: dict[str, list[dict]] = {}
    if workspace_ids:
        for row in conn.execute(
            "select workspace_id, entity_key, version, definition_json from versioned_entities "
            "where entity_type='node_code' and status='published' and workspace_id = any(%s)",
            (list(workspace_ids),),
        ).fetchall():
            workspace_rows.setdefault(str(row["entity_key"]), []).append(row)
    global_rows = {
        str(row["entity_key"]): row
        for row in conn.execute(
            "select entity_key, version, definition_json from versioned_entities "
            "where entity_type='node_code' and status='published' and workspace_id is null"
        ).fetchall()
    }

    seen: set[tuple[str, str]] = set()
    for workflow in workflows:
        workflow_key = workflow["key"]
        definition = workflow.get("definition")
        if not isinstance(definition, dict):
            continue
        for node_key, node in (definition.get("nodes") or {}).items():
            if not isinstance(node, dict):
                continue
            capability = str(node.get("capability") or "")
            if not capability or (workflow_key, node_key) in seen:
                continue
            seen.add((workflow_key, str(node_key)))
            label = f"{workflow_key}/{node_key}"
            base = {
                "workflow_key": workflow_key,
                "node_key": str(node_key),
                "capability": capability,
                "change_note": change_note,
            }
            override = overrides.get(capability)
            if override is not None:
                path = Path(override)
                if not path.is_file():
                    warnings.append(f"{label}: --node-code source missing: {path}")
                    continue
                code = path.read_text(encoding="utf-8")
                entries.append(
                    {
                        **base,
                        "code": code,
                        "code_sha256": sha256_text(code),
                        "source_file": override,
                    }
                )
                continue
            entity_key = f"{workflow_key}:{node_key}"
            candidates = workspace_rows.get(entity_key) or []
            codes = {
                str(json.loads(row["definition_json"]).get("code") or "") for row in candidates
            }
            codes.discard("")
            if len(codes) > 1:
                warnings.append(
                    f"{label}: conflicting published code across source workspaces; "
                    "skipped — reconcile or pass --node-code"
                )
                continue
            if len(codes) == 1:
                row = candidates[0]
                code = codes.pop()
                entries.append(
                    {
                        **base,
                        "code": code,
                        "code_sha256": sha256_text(code),
                        "source_workspace": row["workspace_id"],
                        "source_version": row["version"],
                    }
                )
                continue
            global_row = global_rows.get(entity_key)
            if global_row is not None:
                code = str(json.loads(global_row["definition_json"]).get("code") or "")
                if code:
                    entries.append(
                        {
                            **base,
                            "code": code,
                            "code_sha256": sha256_text(code),
                            "source_workspace": None,
                            "source_version": global_row["version"],
                        }
                    )

    override_capabilities = set(overrides)
    used = {entry["capability"] for entry in entries if entry.get("source_file")}
    for capability in sorted(override_capabilities - used):
        target = None
        for workflow in workflows:
            definition = workflow.get("definition")
            if isinstance(definition, dict):
                target = node_key_for_capability(definition, capability)
                if target is not None:
                    break
        if target is None:
            warnings.append(
                f"--node-code {capability}: capability not found in exported workflow definitions"
            )
    return entries, warnings


def export_skills(conn: Any) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    rows = conn.execute(
        "select key, value from global_settings where key in ('skill_sources', 'skill_lock')"
    ).fetchall()
    documents = {row["key"]: json.loads(row["value"]) for row in rows}
    sources = documents.get("skill_sources")
    lock = documents.get("skill_lock")
    if sources is None:
        warnings.append("global_settings has no skill_sources document")
    if lock is None:
        warnings.append("global_settings has no skill_lock document")
    return {"sources": (sources or {}).get("skills") or {}, "lock": lock or {}}, warnings


def parse_node_code_override(spec: str) -> tuple[str, str]:
    capability, sep, path = spec.partition("=")
    if not sep or not capability.strip() or not path.strip():
        raise SystemExit(f"--node-code spec must be 'capability=path': {spec!r}")
    return capability.strip(), path.strip()


def build_seed(
    conn: Any,
    workflow_keys: list[str],
    workspace_ids: list[str] | None,
    overrides: dict[str, str],
    change_note: str = DEFAULT_CHANGE_NOTE,
) -> tuple[dict, list[str]]:
    workflows, w1 = export_workflows(conn, workflow_keys)
    source_workspaces, w2 = resolve_source_workspaces(conn, workflow_keys, workspace_ids)
    agents, w3 = export_agents(conn, workflows, source_workspaces)
    node_codes, w4 = export_node_codes(conn, workflows, source_workspaces, overrides, change_note)
    skills, w5 = export_skills(conn)
    warnings = w1 + w2 + w3 + w4 + w5
    # A capability that ends up with neither an Agent nor a node code cannot
    # be routed on the target instance — flag it (a code capability normally
    # has no Agent, so this check belongs here, not in export_agents).
    covered = {a["capability"] for a in agents} | {n["capability"] for n in node_codes}
    for workflow in workflows:
        definition = workflow.get("definition")
        if not isinstance(definition, dict):
            continue
        for capability in sorted(workflow_capabilities(definition) - covered):
            warnings.append(
                f"capability {capability}: neither a published agent nor node code "
                "found in source; unroutable on the target instance"
            )
    seed = {
        "schema_version": SEED_SCHEMA_VERSION,
        "workflows": workflows,
        "agents": agents,
        "node_codes": node_codes,
        "skills": skills,
    }
    return seed, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="source Postgres DSN (read-only)")
    parser.add_argument(
        "--workflow",
        action="append",
        required=True,
        help="workflow key to export; repeatable",
    )
    parser.add_argument(
        "--workspace",
        action="append",
        default=None,
        help="source workspace for agents/node codes; repeatable; "
        "default: all workspaces bound to the exported workflows",
    )
    parser.add_argument(
        "--node-code",
        action="append",
        default=[],
        metavar="CAPABILITY=PATH",
        help="override a node's code text with a local file; repeatable",
    )
    parser.add_argument(
        "--forbid-import",
        action="append",
        default=None,
        metavar="PREFIX",
        help="import prefix forbidden in node code; repeatable; "
        f"default: {', '.join(DEFAULT_FORBIDDEN_IMPORT_PREFIXES)}",
    )
    parser.add_argument("--change-note", default=DEFAULT_CHANGE_NOTE)
    parser.add_argument("--output", type=Path, default=Path("seed.json"))
    args = parser.parse_args()

    forbidden = (
        tuple(args.forbid_import)
        if args.forbid_import is not None
        else DEFAULT_FORBIDDEN_IMPORT_PREFIXES
    )
    overrides = dict(parse_node_code_override(spec) for spec in args.node_code)

    with psycopg.connect(args.dsn, row_factory=dict_row) as conn:
        seed, warnings = build_seed(
            conn, args.workflow, args.workspace, overrides, args.change_note
        )
    seed["exported_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    seed["source"] = {"database": _masked_dsn(args.dsn)}
    seed["export_warnings"] = warnings

    problems = validate_seed(seed, forbidden)
    print(
        f"workflows: {len(seed['workflows'])}  agents: {len(seed['agents'])}  "
        f"node_codes: {len(seed['node_codes'])}  "
        f"skills: {len(seed['skills'].get('sources') or {})}"
    )
    if warnings:
        print("export warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if problems:
        print("seed validation FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.output} ({args.output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
