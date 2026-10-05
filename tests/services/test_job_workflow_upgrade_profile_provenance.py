"""Upgrade diff normalization across the v93 Agent profile backfill (#935).

An old job froze a legacy agent node; v93 inlined the Agent definition into
the active revision's node. Inherit upgrade must not re-run the node just
because it was inlined — provided the old run executed exactly the inlined
definition (provenance ``definition_hash``) and the old node equals the new
node's legacy view. Provenance must also survive later publishes for nodes
whose profile is unchanged, and drop out once the profile is edited.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from server.app.agent_catalog import AgentDefinition
from server.app.db.migrations.agent_profile_backfill import migrate_agent_profile_backfill
from server.app.db.transaction import write_transaction
from server.app.services.agent_profile_provenance import provenance_from_revision_json
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_dict
from tests.helpers import replace_agent_catalog
from tests.helpers.job_workflow_upgrade import (
    make_upgrade_service,
    publish_node_code,
    seed_done_execution,
    seed_wfchain_job,
    setup_wfchain_env,
    wfchain_definition,
)
from tests.postgres_support import TEST_DATABASE_URL

_V1 = AgentDefinition(capability="cap_b", runtime="pi", tools=("read",))


def _legacy_agent_chain():
    definition = wfchain_definition({"a": ["a_out.json"], "b": ["b_out.json"]})
    nodes = dict(definition.nodes)
    # Node tools override the definition's: the inlined profile hash then
    # differs from the definition hash, so only the provenance identity
    # bypass (not a coincidental hash match) can keep b inherited (#935 D2).
    nodes["b"] = dataclasses.replace(definition.nodes["b"], node_type="agent", tools=("bash",))
    return dataclasses.replace(definition, nodes=nodes)


def _backfilled_env(tmp_path: Path, *, executed_hash: str):
    """Legacy job on v1, active v2 inlined by v93, b executed with *executed_hash*."""
    definition = _legacy_agent_chain()
    queries, workspace, revisions, original = setup_wfchain_env(tmp_path, definition)
    replace_agent_catalog(workspace["id"], {"agent-b": _V1})
    revisions.publish_workspace_revision(workspace["id"], definition)
    a_hash = publish_node_code(queries, workspace["id"], "a", "def run(ctx):\n    return {}\n")
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_agent_profile_backfill(conn)
    job_id = seed_wfchain_job(queries, workspace, original, ["a", "b", "c"])
    for key in ("a", "b", "c"):
        queries.update_job_node(job_id, key, status="pending")
    seed_done_execution(queries, workspace["id"], job_id, "a", kind="code", impl_hash=a_hash)
    seed_done_execution(
        queries, workspace["id"], job_id, "b", kind="agent", impl_hash=executed_hash
    )
    queries.update_job_status(job_id, "completed")
    job_dir = resolve_job_dir(queries.get_job(job_id), queries.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "a_out.json").write_text("old-a")
    (job_dir / "b_out.json").write_text("old-b")
    return queries, workspace, revisions, job_id


def _statuses(queries, job_id: str) -> dict[str, str]:
    return {node["node_key"]: node["status"] for node in queries.list_job_nodes(job_id)}


def test_inlined_node_is_inherited_when_the_old_run_executed_its_definition(
    tmp_path: Path,
) -> None:
    queries, workspace, _, job_id = _backfilled_env(tmp_path, executed_hash=_V1.definition_hash())
    active = queries.get_active_workflow_revision(workspace["id"], "wfchain")
    assert (
        workflow_definition_from_dict(json.loads(active["definition_json"]))
        .nodes["b"]
        .execution.runtime
        == "pi"
    )
    # D4: republishing the Agent no longer reaches the inlined node.
    replace_agent_catalog(
        workspace["id"], {"agent-b": AgentDefinition(capability="cap_b", runtime="velites")}
    )

    result = make_upgrade_service(tmp_path, queries).upgrade(
        workspace["id"], job_id, mode="inherit"
    )

    assert result["status"] == "succeeded"
    statuses = _statuses(queries, job_id)
    assert statuses["a"] == "completed"
    assert statuses["b"] == "completed"
    assert statuses["c"] == "pending"  # no execution record → conservative re-run


def test_inlined_node_reruns_when_the_old_run_executed_another_definition(
    tmp_path: Path,
) -> None:
    other = AgentDefinition(capability="cap_b", runtime="velites").definition_hash()
    queries, workspace, _, job_id = _backfilled_env(tmp_path, executed_hash=other)

    make_upgrade_service(tmp_path, queries).upgrade(workspace["id"], job_id, mode="inherit")

    statuses = _statuses(queries, job_id)
    assert statuses["a"] == "completed"
    assert statuses["b"] == "pending"


def test_provenance_survives_publish_only_for_unchanged_profiles(tmp_path: Path) -> None:
    queries, workspace, revisions, _ = _backfilled_env(
        tmp_path, executed_hash=_V1.definition_hash()
    )
    active = queries.get_active_workflow_revision(workspace["id"], "wfchain")
    inlined = workflow_definition_from_dict(json.loads(active["definition_json"]))
    assert set(provenance_from_revision_json(active["definition_json"])) == {"b"}

    # A structural publish that leaves b's profile alone keeps the entry.
    relabeled = dataclasses.replace(
        inlined, nodes={**inlined.nodes, "c": dataclasses.replace(inlined.nodes["c"], after=[])}
    )
    kept = revisions.publish_workspace_revision(workspace["id"], relabeled)
    assert set(provenance_from_revision_json(kept["definition_json"])) == {"b"}

    # Editing b's profile (tools) drops it: normalization never vouches for edits.
    edited = dataclasses.replace(
        relabeled,
        nodes={
            **relabeled.nodes,
            "b": dataclasses.replace(relabeled.nodes["b"], tools=("read", "bash")),
        },
    )
    dropped = revisions.publish_workspace_revision(workspace["id"], edited)
    assert provenance_from_revision_json(dropped["definition_json"]) == {}


def test_runtime_only_save_keeps_provenance(tmp_path: Path) -> None:
    queries, workspace, revisions, _ = _backfilled_env(
        tmp_path, executed_hash=_V1.definition_hash()
    )
    active = queries.get_active_workflow_revision(workspace["id"], "wfchain")
    inlined = workflow_definition_from_dict(json.loads(active["definition_json"]))
    b = inlined.nodes["b"]
    runtime_edit = dataclasses.replace(
        inlined,
        nodes={
            **inlined.nodes,
            "b": dataclasses.replace(b, execution=dataclasses.replace(b.execution, model="m2")),
        },
    )

    saved = revisions.save_workspace_revision(workspace["id"], runtime_edit)

    assert saved["id"] == active["id"]  # in-place runtime edit, not a new revision
    assert set(provenance_from_revision_json(saved["definition_json"])) == {"b"}
