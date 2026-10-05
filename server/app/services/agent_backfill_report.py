"""Agent retirement backfill dry-run report (#934, #440 PR-3 of the 0.7.16 slice).

Read-only: walks each workspace's active revision and Studio draft, runs
the per-node simulation (``agent_backfill_plan``) against the workspace's
legacy catalog loaded through the profile facade (``legacy_agent_catalog``),
and aggregates what the 0.7.17 data migration would do:

* ``workspaces[].sources[].nodes`` — per agent node: backfilled runtime /
  tools / config_schema / skill / requires_labels, or why it is unresolved;
* ``shared_definitions`` — one definition serving several nodes of the same
  source (the migration expands it 1:N);
* ``unresolved`` — nodes left untouched (no / several published Agents,
  archived, draft-only);
* ``config_schema_overrides`` — node declarations the overwrite discards.

The report is deterministic (sorted, no timestamps) so a fixture workspace
pins it byte for byte. It is written to stdout or a local file only —
never committed (open-source hygiene: it reflects a deployment's data).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from server.app.services.agent_backfill_plan import plan_definition_backfill
from server.app.services.agent_node_profile import build_capability_index
from server.app.services.agent_node_profile_catalog import legacy_agent_catalog
from server.app.services.agent_service import AgentService
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.workflows.definition import WorkflowDefinitionError, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

REPORT_KIND = "agent-definition-backfill-dry-run"
SOURCE_ACTIVE = "active_revision"
SOURCE_DRAFT = "draft"


def _sources(reader: JobQueries, workspace_id: str) -> Iterable[dict[str, Any]]:
    """(source descriptor, parse thunk) for the active revision and the draft."""
    revision = reader.get_active_workflow_revision(workspace_id, workspace_id)
    if revision is not None:
        yield {
            "source": SOURCE_ACTIVE,
            "revision_id": str(revision["id"]),
            "revision_version": int(revision["version"]),
            "_parse": lambda: workflow_definition_from_dict(
                json.loads(str(revision["definition_json"]))
            ),
        }
    draft = reader.get_workspace_workflow_draft(workspace_id)
    if draft is not None:
        yield {
            "source": SOURCE_DRAFT,
            "_parse": lambda: workflow_definition_from_yaml_string(str(draft["definition_yaml"])),
        }


def _workspace_report(reader: JobQueries, workspace_id: str) -> dict[str, Any]:
    catalog = legacy_agent_catalog(reader, workspace_id)
    index = build_capability_index(catalog)
    unpublished = {
        entity.entity_key: (str(entity.definition.get("capability") or ""), entity.status)
        for entity in AgentService(reader, workspace_id).list_latest()
        if entity.entity_key not in catalog
    }
    sources: list[dict[str, Any]] = []
    for descriptor in _sources(reader, workspace_id):
        parse = descriptor.pop("_parse")
        try:
            definition = parse()
        except (WorkflowDefinitionError, ValueError) as exc:
            sources.append({**descriptor, "error": str(exc), "nodes": []})
            continue
        nodes = plan_definition_backfill(definition, catalog, index, unpublished)
        sources.append({**descriptor, "error": None, "nodes": nodes})
    return {
        "workspace_id": workspace_id,
        "published_agents": sorted(catalog),
        "sources": sources,
    }


def _aggregate(workspaces: list[dict[str, Any]]) -> dict[str, Any]:
    shared: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    overrides: list[dict[str, Any]] = []
    backfill_count = 0
    for workspace in workspaces:
        ws = workspace["workspace_id"]
        for source in workspace["sources"]:
            groups: dict[str, list[str]] = {}
            for node in source["nodes"]:
                ref = {"workspace_id": ws, "source": source["source"], "node_key": node["node_key"]}
                if node["status"] == "unresolved":
                    unresolved.append(
                        {**ref, **{k: node[k] for k in ("capability", "reason", "agent_ids")}}
                    )
                    continue
                if node["status"] != "backfill":
                    continue
                backfill_count += 1
                groups.setdefault(node["agent_id"], []).append(node["node_key"])
                if node["config_schema_diff"] is not None:
                    overrides.append(
                        {**ref, "agent_id": node["agent_id"], **node["config_schema_diff"]}
                    )
            shared.extend(
                {
                    "workspace_id": ws,
                    "source": source["source"],
                    "agent_id": agent_id,
                    "node_keys": keys,
                }
                for agent_id, keys in sorted(groups.items())
                if len(keys) > 1
            )
    return {
        "summary": {
            "workspaces": len(workspaces),
            "source_errors": sum(1 for w in workspaces for s in w["sources"] if s["error"]),
            "agent_nodes_backfilled": backfill_count,
            "agent_nodes_unresolved": len(unresolved),
            "shared_definition_groups": len(shared),
            "config_schema_overrides": len(overrides),
        },
        "shared_definitions": shared,
        "unresolved": unresolved,
        "config_schema_overrides": overrides,
    }


def build_agent_backfill_report(
    reader: JobQueries, workspace_ids: Iterable[str] | None = None
) -> dict[str, Any]:
    """The full dry-run report; *workspace_ids* narrows the walk (unknown ids skipped)."""
    known = sorted(str(row["id"]) for row in reader.list_workspaces())
    wanted = known if workspace_ids is None else sorted(set(workspace_ids) & set(known))
    workspaces = [_workspace_report(reader, ws) for ws in wanted]
    return {"report": REPORT_KIND, **_aggregate(workspaces), "workspaces": workspaces}


def render_report_json(report: Mapping[str, Any]) -> str:
    """Canonical JSON (sorted keys) — the byte-stable form tests pin."""
    return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
