"""Quality replays: copy-job creation, frozen inputs, version pins (schema v29)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.db.transaction import write_transaction
from server.app.services.agent_service import AgentService
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.services.quality_labels import QualityLabelService
from server.app.services.quality_replays import QualityReplayService
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.helpers import seed_workspace_agent_definitions
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.fresh_schema

AGENT_ID = "example-write-script-v1"  # demo template, seeded per workspace (v46)
CAPABILITY = "write_script"


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={
            "intake": WorkflowNode(
                key="intake",
                label="intake",
                capability="intake_items",
                outputs=["question.json"],
            ),
            "generate": WorkflowNode(
                key="generate",
                label="generate",
                capability=CAPABILITY,
                after=["intake"],
                inputs=["question.json"],
                outputs=["key_info.json"],
            ),
            "assemble": WorkflowNode(
                key="assemble",
                label="assemble",
                capability="assemble",
                after=["generate"],
                inputs=["key_info.json"],
                outputs=["final.json"],
            ),
        },
    )


class _Env:
    def __init__(self, job_db, tmp_path: Path, route_kind: str) -> None:
        self.job_db = job_db
        self.tmp_path = tmp_path
        ws = job_db.create_workspace(
            default_workflow_key="education_video_problems_generation", name="Replay WS"
        )
        self.workspace_id = str(ws["id"])
        definition = _definition()
        snapshot = serialize_definition(definition)
        self.job = job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id="Q1",
            batch_id="",
            title="Q1",
            node_keys=list(definition.nodes),
            workspace_id=self.workspace_id,
            workflow_revision_id="rev-1",
            workflow_version=3,
            workflow_definition_hash=definition_hash(snapshot),
            workflow_definition_snapshot_json=snapshot,
        )
        self.job_id = str(self.job["id"])
        job_dir = resolve_job_dir(self.job, job_db.jobs_dir)
        (job_dir / "question.json").write_text('{"q": 1}', encoding="utf-8")
        (job_dir / "key_info.json").write_text('{"key": true}', encoding="utf-8")
        target_id = AGENT_ID if route_kind == "agent" else "code-default"
        if route_kind == "agent":
            # Agent definitions are workspace-scoped (schema v46): the demo
            # templates seed into this workspace before routes reference them.
            seed_workspace_agent_definitions(self.workspace_id)
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute(
                "insert into workspace_node_routes("
                "workspace_id, workflow_key, node_key, target_kind, target_id)"
                " values (%s, 'test', 'generate', %s, %s)",
                (self.workspace_id, route_kind, target_id),
            )
            conn.execute(
                "update job_nodes set status='completed', finished_at=current_timestamp"
                " where job_id=%s",
                (self.job_id,),
            )
            run = conn.execute(
                "insert into node_runs(job_id, node_key, status) values (%s, 'generate', 'completed')"
                " returning id",
                (self.job_id,),
            ).fetchone()
            conn.execute(
                "insert into quality_sample_batches(id, workspace_id, name, sample_size)"
                " values ('batch-1', %s, 'batch', 10)",
                (self.workspace_id,),
            )
            conn.execute(
                "insert into quality_sample_items("
                "id, batch_id, node_run_id, job_id, node_key, capability)"
                " values ('item-1', 'batch-1', %s, %s, 'generate', %s)",
                (run["id"], self.job_id, CAPABILITY),
            )

    def service(self, artifact_store: ArtifactStore | None = None) -> QualityReplayService:
        return QualityReplayService(self.job_db, artifact_store)

    def node_statuses(self, job_id: str) -> dict[str, str]:
        return {row["node_key"]: row["status"] for row in self.job_db.list_job_nodes(job_id)}

    def set_copy_node_status(self, job_id: str, status: str, error: str = "") -> None:
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute(
                "update job_nodes set status=%s, error_message=%s,"
                " finished_at=current_timestamp where job_id=%s and node_key='generate'",
                (status, error, job_id),
            )

    def copy_batch_payload(self, replay: dict) -> dict:
        copy_job = self.job_db.get_job(str(replay["replay_job_id"]))
        batch = self.job_db.get_batch(str(copy_job["batch_id"]))
        return json.loads(str(batch["source_payload_json"]))


@pytest.fixture
def env(job_db, tmp_path):
    return _Env(job_db, tmp_path, "handler_executor")


@pytest.fixture
def agent_env(job_db, tmp_path):
    return _Env(job_db, tmp_path, "agent")


def test_create_replay_builds_isolated_copy_job(env) -> None:
    replay = env.service().create_replay(env.workspace_id, "item-1", created_by="user:test")

    assert replay["status"] == "pending"
    assert replay["agent_version"] is None
    copy_job_id = str(replay["replay_job_id"])
    assert copy_job_id != env.job_id
    assert f"replay-{replay['id']}" in copy_job_id

    statuses = env.node_statuses(copy_job_id)
    assert statuses == {"intake": "completed", "generate": "pending", "assemble": "not_applicable"}

    # Frozen input was copied; the original job is untouched.
    copy_job = env.job_db.get_job(copy_job_id)
    copied = resolve_job_dir(copy_job, env.job_db.jobs_dir) / "question.json"
    assert copied.read_text(encoding="utf-8") == '{"q": 1}'
    assert not (resolve_job_dir(copy_job, env.job_db.jobs_dir) / "key_info.json").exists()
    original_dir = resolve_job_dir(env.job, env.job_db.jobs_dir)
    assert (original_dir / "key_info.json").read_text(encoding="utf-8") == '{"key": true}'
    assert env.node_statuses(env.job_id) == {
        "intake": "completed",
        "generate": "completed",
        "assemble": "completed",
    }

    # The copy job shares the original's frozen workflow snapshot.
    assert copy_job["workflow_definition_hash"] == env.job["workflow_definition_hash"]
    assert (
        copy_job["workflow_definition_snapshot_json"]
        == (env.job["workflow_definition_snapshot_json"])
    )


def test_replay_status_reconciles_from_copy_job(env) -> None:
    service = env.service()
    replay = service.create_replay(env.workspace_id, "item-1")
    copy_job_id = str(replay["replay_job_id"])

    env.set_copy_node_status(copy_job_id, "running")
    (listed,) = service.list_replays(env.workspace_id, "item-1")
    assert listed["status"] == "running"

    env.set_copy_node_status(copy_job_id, "failed", "boom")
    detail = service.get_replay_detail(env.workspace_id, str(replay["id"]))
    assert detail["replay"]["status"] == "failed"
    assert detail["replay"]["error_message"] == "boom"
    assert detail["replay"]["finished_at"] is not None

    # Terminal rows stop reconciling even if the copy job is later cleaned up.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from jobs where id=%s", (copy_job_id,))
    detail = service.get_replay_detail(env.workspace_id, str(replay["id"]))
    assert detail["replay"]["status"] == "failed"


def test_replay_success_converges_copy_job(env) -> None:
    service = env.service()
    replay = service.create_replay(env.workspace_id, "item-1")
    copy_job_id = str(replay["replay_job_id"])
    env.set_copy_node_status(copy_job_id, "completed")
    detail = service.get_replay_detail(env.workspace_id, str(replay["id"]))
    assert detail["replay"]["status"] == "succeeded"
    assert detail["replay"]["finished_at"] is not None


def test_second_active_replay_rejected(env) -> None:
    service = env.service()
    service.create_replay(env.workspace_id, "item-1")
    with pytest.raises(ConflictError):
        service.create_replay(env.workspace_id, "item-1")

    # Once the first replay reaches a terminal state a new one is allowed.
    replay = service.list_replays(env.workspace_id, "item-1")[0]
    env.set_copy_node_status(str(replay["replay_job_id"]), "completed")
    again = service.create_replay(env.workspace_id, "item-1")
    assert again["id"] != replay["id"]
    assert len(service.list_replays(env.workspace_id, "item-1")) == 2


def test_missing_frozen_input_rejected(env) -> None:
    resolve_job_dir(env.job, env.job_db.jobs_dir).joinpath("question.json").unlink()
    with pytest.raises(InvalidOperationError, match="frozen inputs"):
        env.service().create_replay(env.workspace_id, "item-1")
    # The failed attempt is recorded and does not block a later retry.
    (replay,) = env.service().list_replays(env.workspace_id, "item-1")
    assert replay["status"] == "failed"
    assert "frozen inputs" in replay["error_message"]


def test_missing_original_job_rejected(env) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from jobs where id=%s", (env.job_id,))
    with pytest.raises(InvalidOperationError, match="original job no longer exists"):
        env.service().create_replay(env.workspace_id, "item-1")


def test_missing_snapshot_rejected(env) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update jobs set workflow_definition_snapshot_json='' where id=%s", (env.job_id,)
        )
    with pytest.raises(InvalidOperationError, match="frozen workflow snapshot"):
        env.service().create_replay(env.workspace_id, "item-1")


def test_unknown_item_and_cross_workspace_rejected(env) -> None:
    with pytest.raises(NotFoundError):
        env.service().create_replay(env.workspace_id, "missing")
    with pytest.raises(NotFoundError):
        env.service().create_replay("ws-other", "item-1")


def test_agent_version_pin_rejected_for_executor_node(env) -> None:
    with pytest.raises(InvalidOperationError, match="Agent-routed nodes only"):
        env.service().create_replay(env.workspace_id, "item-1", agent_version=1)


def _save_draft(workspace_id: str, capability: str = CAPABILITY) -> AgentDefinition:
    definition = AgentDefinition(
        capability=capability,
        runtime="velites",
        skill="education-video-problems-generation/write-script",
        tools=("read",),
    )
    AgentService(TEST_DATABASE_URL, workspace_id).save_draft(
        AGENT_ID, definition, created_by="test"
    )
    return definition


def test_default_pin_uses_current_published(agent_env) -> None:
    replay = agent_env.service().create_replay(agent_env.workspace_id, "item-1")
    assert replay["agent_id"] == AGENT_ID
    assert replay["agent_version"] == 1
    pin = agent_env.copy_batch_payload(replay)["agent_versions"]["generate"]
    assert pin["agent_id"] == AGENT_ID
    assert pin["version"] == 1
    published = AgentService(TEST_DATABASE_URL, agent_env.workspace_id).get_published(AGENT_ID)
    assert pin["definition_hash"] == published.definition_hash


def test_explicit_draft_version_pin(agent_env) -> None:
    draft = _save_draft(agent_env.workspace_id)
    replay = agent_env.service().create_replay(agent_env.workspace_id, "item-1", agent_version=2)
    assert replay["agent_version"] == 2
    pin = agent_env.copy_batch_payload(replay)["agent_versions"]["generate"]
    assert pin["version"] == 2
    assert pin["definition_hash"] == draft.definition_hash()


def test_archived_version_pin_allowed(agent_env) -> None:
    service = AgentService(TEST_DATABASE_URL, agent_env.workspace_id)
    published_v1 = service.get_published(AGENT_ID)
    _save_draft(agent_env.workspace_id)
    service.publish(AGENT_ID)  # v2 published, v1 archived
    replay = agent_env.service().create_replay(agent_env.workspace_id, "item-1", agent_version=1)
    pin = agent_env.copy_batch_payload(replay)["agent_versions"]["generate"]
    assert pin["version"] == 1
    assert pin["definition_hash"] == published_v1.definition_hash


def test_unknown_version_rejected(agent_env) -> None:
    with pytest.raises(NotFoundError, match="no version 99"):
        agent_env.service().create_replay(agent_env.workspace_id, "item-1", agent_version=99)


def test_capability_mismatch_pin_rejected(agent_env) -> None:
    _save_draft(agent_env.workspace_id, capability="some_other_capability")
    with pytest.raises(InvalidOperationError, match="does not match node capability"):
        agent_env.service().create_replay(agent_env.workspace_id, "item-1", agent_version=2)


def test_upstream_artifact_refs_shared_with_copy(env, tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
    digest = store.put(b'{"q": 1}')
    store.add_ref(env.job_id, "intake", "question.json", digest)
    store.add_ref(env.job_id, "generate", "key_info.json", store.put(b'{"key": true}'))

    service = env.service(store)
    replay = service.create_replay(env.workspace_id, "item-1")
    refs = {
        (ref["node_key"], ref["name"]): ref["hash"]
        for ref in store.refs_for_job(str(replay["replay_job_id"]))
    }
    # Upstream refs are shared (same content hash); the target node's own
    # outputs are not pre-seeded.
    assert refs == {("intake", "question.json"): digest}

    detail = service.get_replay_detail(env.workspace_id, str(replay["id"]))
    assert [a["name"] for a in detail["input_artifacts"]] == ["question.json"]
    assert detail["input_artifacts"][0]["content"] == '{"q": 1}'
    assert detail["artifacts"] == []


def test_replay_labels(env) -> None:
    service = env.service()
    replay = service.create_replay(env.workspace_id, "item-1")
    labels = QualityLabelService(TEST_DATABASE_URL)

    with pytest.raises(InvalidOperationError, match="require a replay_id"):
        labels.add_label(env.workspace_id, "item-1", verdict="good", target="replay")
    with pytest.raises(NotFoundError, match="Replay not found"):
        labels.add_label(
            env.workspace_id, "item-1", verdict="good", target="replay", replay_id="missing"
        )

    label = labels.add_label(
        env.workspace_id,
        "item-1",
        verdict="bad",
        reason_codes=["fact_error"],
        target="replay",
        replay_id=str(replay["id"]),
    )
    assert label["target"] == "replay"
    assert label["replay_id"] == replay["id"]

    detail = service.get_replay_detail(env.workspace_id, str(replay["id"]))
    assert [row["verdict"] for row in detail["labels"]] == ["bad"]
