"""Quality replay by workflow revision / draft profile (#1079, #440 D6).

Pins the D6 replay contract: an agent node replays with the execution
profile of a chosen workflow revision (or the Studio draft) — transplanted
into the copy job's snapshot with its own definition hash — and the copy run
freezes ``node_profiles[node_key] = {revision_id, node_key, profile_hash}``;
no choice replays the original snapshot profile; the legacy Agent-version
pin is rejected for self-contained nodes; code nodes replay without a route.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from server.app.db.transaction import write_transaction
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.node_profile_pins import node_profile_hash
from server.app.services.quality_replays import QualityReplayService
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
    WorkflowNodeExecution,
)
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.fresh_schema


def _definition(execution: WorkflowNodeExecution | None, *, tools=()) -> WorkflowDefinition:
    generate = WorkflowNode(
        key="generate",
        label="generate",
        capability="write_script",
        node_type="agent",
        after=["intake"],
        inputs=["question.json"],
        outputs=["key_info.json"],
        tools=tuple(tools),
    )
    if execution is not None:
        generate = replace(generate, execution=execution)
    return WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={
            "intake": WorkflowNode(
                key="intake", label="intake", capability="intake_items", outputs=["question.json"]
            ),
            "generate": generate,
        },
    )


_ORIGINAL = WorkflowNodeExecution(runtime="velites", provider="p1", model="m1")
_CANDIDATE = WorkflowNodeExecution(runtime="pi", provider="p2", model="m2")


class _Env:
    def __init__(self, job_db, snapshot_def: WorkflowDefinition) -> None:
        self.job_db = job_db
        ws = job_db.create_workspace(name="Profile replay WS")
        self.workspace_id = str(ws["id"])
        snapshot = serialize_definition(snapshot_def)
        self.job = job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id="Q1",
            run_id="",
            title="Q1",
            node_keys=list(snapshot_def.nodes),
            workspace_id=self.workspace_id,
            workflow_revision_id=f"{self.workspace_id}:v1",
            workflow_version=1,
            workflow_definition_hash=definition_hash(snapshot),
            workflow_definition_snapshot_json=snapshot,
        )
        job_dir = resolve_job_dir(self.job, job_db.jobs_dir)
        (job_dir / "question.json").write_text('{"q": 1}', encoding="utf-8")
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute(
                "update job_nodes set status='completed' where job_id=%s", (self.job["id"],)
            )
            run = conn.execute(
                "insert into node_runs(job_id, node_key, status) values (%s, 'generate',"
                " 'completed') returning id",
                (self.job["id"],),
            ).fetchone()
            conn.execute(
                "insert into quality_sample_batches(id, workspace_id, name, sample_size)"
                " values ('batch-1', %s, 'batch', 10)",
                (self.workspace_id,),
            )
            conn.execute(
                "insert into quality_sample_items(id, batch_id, node_run_id, job_id, node_key,"
                " capability) values ('item-1', 'batch-1', %s, %s, 'generate', 'write_script')",
                (run["id"], self.job["id"]),
            )

    def add_revision(self, version: int, definition: WorkflowDefinition, status: str) -> str:
        revision_id = f"{self.workspace_id}:v{version}"
        text = serialize_definition(definition)
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute(
                "insert into workflow_revisions(id, workspace_id, version, status,"
                " definition_json, definition_hash, published_at)"
                " values (%s, %s, %s, %s, %s, %s, current_timestamp)",
                (revision_id, self.workspace_id, version, status, text, definition_hash(text)),
            )
        return revision_id

    def service(self) -> QualityReplayService:
        return QualityReplayService(self.job_db)

    def copy(self, replay: dict) -> tuple[dict, dict]:
        copy_job = self.job_db.get_job(str(replay["replay_job_id"]))
        run = self.job_db.get_run(str(copy_job["run_id"]))
        return copy_job, json.loads(str(run["frozen_pins_json"]))


def _copy_node(copy_job: dict) -> WorkflowNode:
    payload = json.loads(str(copy_job["workflow_definition_snapshot_json"]))
    return workflow_definition_from_dict(payload).nodes["generate"]


def test_default_replays_the_original_snapshot_profile(job_db) -> None:
    env = _Env(job_db, _definition(_ORIGINAL))
    original_node = _definition(_ORIGINAL).nodes["generate"]

    replay = env.service().create_replay(env.workspace_id, "item-1")

    copy_job, pins = env.copy(replay)
    # No transplant: the copy shares the original snapshot and its hash.
    assert copy_job["workflow_definition_hash"] == env.job["workflow_definition_hash"]
    assert pins["agent_versions"] == {}
    assert pins["node_profiles"] == {
        "generate": {
            "revision_id": f"{env.workspace_id}:v1",
            "node_key": "generate",
            "profile_hash": node_profile_hash(original_node),
        }
    }
    assert replay["agent_version"] is None
    assert replay["profile_hash"] == node_profile_hash(original_node)


def test_revision_choice_transplants_that_profile(job_db) -> None:
    env = _Env(job_db, _definition(_ORIGINAL))
    revision_id = env.add_revision(2, _definition(_CANDIDATE, tools=["read"]), "active")

    replay = env.service().create_replay(env.workspace_id, "item-1", revision_id=revision_id)

    copy_job, pins = env.copy(replay)
    node = _copy_node(copy_job)
    assert node.execution.runtime == "pi"
    assert node.execution.model == "m2"
    assert node.tools == ("read",)
    # The rewritten snapshot carries its own hash (definition cache is hash-keyed).
    assert copy_job["workflow_definition_hash"] == definition_hash(
        str(copy_job["workflow_definition_snapshot_json"])
    )
    assert copy_job["workflow_definition_hash"] != env.job["workflow_definition_hash"]
    pin = pins["node_profiles"]["generate"]
    assert pin == {
        "revision_id": revision_id,
        "node_key": "generate",
        "profile_hash": node_profile_hash(node),
    }
    assert replay["revision_id"] == revision_id
    assert replay["revision_version"] == 2
    # The original job is untouched.
    assert (
        json.loads(str(job_db.get_job(env.job["id"])["workflow_definition_snapshot_json"]))[
            "nodes"
        ]["generate"]["execution"]["model"]
        == "m1"
    )


def test_legacy_snapshot_node_replays_with_an_inlined_revision(job_db) -> None:
    env = _Env(job_db, _definition(None))
    revision_id = env.add_revision(2, _definition(_CANDIDATE), "active")

    replay = env.service().create_replay(env.workspace_id, "item-1", revision_id=revision_id)

    copy_job, pins = env.copy(replay)
    assert _copy_node(copy_job).execution.runtime == "pi"
    assert pins["agent_versions"] == {}
    assert pins["node_profiles"]["generate"]["revision_id"] == revision_id


def test_draft_choice_freezes_the_draft_profile(job_db) -> None:
    env = _Env(job_db, _definition(_ORIGINAL))
    draft = {
        "key": "test",
        "label": "Test",
        "nodes": {
            "intake": {"capability": "intake_items", "outputs": ["question.json"]},
            "generate": {
                "type": "agent",
                "capability": "write_script",
                "after": ["intake"],
                "inputs": ["question.json"],
                "outputs": ["key_info.json"],
                "execution": {"runtime": "pi", "provider": "p3", "model": "m3"},
            },
        },
    }
    job_db.upsert_workspace_workflow_draft(env.workspace_id, yaml.safe_dump(draft))

    replay = env.service().create_replay(env.workspace_id, "item-1", use_draft=True)

    copy_job, pins = env.copy(replay)
    assert _copy_node(copy_job).execution.model == "m3"
    assert pins["node_profiles"]["generate"]["revision_id"] is None
    assert replay["revision_id"] is None


def test_replay_profile_choice_errors(job_db, tmp_path: Path) -> None:
    env = _Env(job_db, _definition(_ORIGINAL))
    legacy_rev = env.add_revision(2, _definition(None), "archived")
    service = env.service()

    with pytest.raises(InvalidOperationError, match="legacy Agent-definition nodes only"):
        service.create_replay(env.workspace_id, "item-1", agent_version=1)
    with pytest.raises(NotFoundError):
        service.create_replay(env.workspace_id, "item-1", revision_id="missing")
    with pytest.raises(InvalidOperationError, match="no self-contained agent node"):
        service.create_replay(env.workspace_id, "item-1", revision_id=legacy_rev)
    with pytest.raises(InvalidOperationError, match="has no workflow draft"):
        service.create_replay(env.workspace_id, "item-1", use_draft=True)
    # A draft that does not load reports the loader error, not a missing node.
    job_db.upsert_workspace_workflow_draft(env.workspace_id, "key: [unclosed")
    with pytest.raises(InvalidOperationError, match="the workflow draft does not load"):
        service.create_replay(env.workspace_id, "item-1", use_draft=True)
    with pytest.raises(InvalidOperationError, match="not both"):
        service.create_replay(env.workspace_id, "item-1", revision_id=legacy_rev, use_draft=True)


def test_replay_profile_options_list_revisions_and_draft(job_db) -> None:
    env = _Env(job_db, _definition(_ORIGINAL))
    env.add_revision(1, _definition(_ORIGINAL), "archived")
    env.add_revision(2, _definition(None), "archived")  # legacy node: not offered
    env.add_revision(3, _definition(_CANDIDATE), "active")

    options = env.service().replay_profile_options(env.workspace_id, "item-1")["options"]

    assert [(o["source"], o["revision_version"]) for o in options] == [
        ("revision", 3),
        ("revision", 1),
    ]
    by_version = {o["revision_version"]: o for o in options}
    assert by_version[1]["is_original"] is True
    assert by_version[3]["is_original"] is False
    assert by_version[3]["runtime"] == "pi"
    assert by_version[3]["model"] == "m2"


def test_code_node_replays_without_any_route(job_db) -> None:
    """A code node has no workspace route since P-0.5; replay runs it unpinned."""
    definition = _definition(None)
    nodes = dict(definition.nodes)
    nodes["generate"] = replace(nodes["generate"], node_type="code")
    env = _Env(job_db, replace(definition, nodes=nodes))

    replay = env.service().create_replay(env.workspace_id, "item-1")

    _copy_job, pins = env.copy(replay)
    assert pins["agent_versions"] == {}
    assert "node_profiles" not in pins or pins["node_profiles"] == {}
    with pytest.raises(InvalidOperationError, match="only agent nodes"):
        env.service().create_replay(env.workspace_id, "item-1", use_draft=True)
