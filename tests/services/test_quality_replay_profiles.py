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
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from server.app.db.pools import pool_for
from server.app.db.transaction import write_transaction
from server.app.jobs import JobQueries
from server.app.services import skill_commit_pins
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.services.node_profile_pins import node_profile_hash
from server.app.services.quality_replays import QualityReplayService
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.skills.errors import SkillRepoError
from server.app.skills.manager import SkillManager
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowNodeSkill,
)
from tests.helpers.skill_git import (
    _KEY,
    _commit_skill_update,
    _head_commit,
    _make_manager,
    _make_skill_repo,
    _tag,
)
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.fresh_schema


def _definition(
    execution: WorkflowNodeExecution | None, *, tools=(), skill: WorkflowNodeSkill | None = None
) -> WorkflowDefinition:
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
    if skill is not None:
        generate = replace(generate, skill=skill)
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
    def __init__(
        self, job_db, snapshot_def: WorkflowDefinition, *, legacy_execution: dict | None = None
    ) -> None:
        self.job_db = job_db
        ws = job_db.create_workspace(name="Profile replay WS")
        self.workspace_id = str(ws["id"])
        snapshot_payload = json.loads(serialize_definition(snapshot_def))
        if legacy_execution is not None:
            snapshot_payload["execution"] = legacy_execution
        snapshot = json.dumps(
            snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
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
            self.node_run_id = int(run["id"])
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

    def add_revision(
        self,
        version: int,
        definition: WorkflowDefinition,
        status: str,
        *,
        legacy_execution: dict | None = None,
    ) -> str:
        revision_id = f"{self.workspace_id}:v{version}"
        payload = json.loads(serialize_definition(definition))
        if legacy_execution is not None:
            payload["execution"] = legacy_execution
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute(
                "insert into workflow_revisions(id, workspace_id, version, status,"
                " definition_json, definition_hash, published_at)"
                " values (%s, %s, %s, %s, %s, %s, current_timestamp)",
                (revision_id, self.workspace_id, version, status, text, definition_hash(text)),
            )
        return revision_id

    def add_node_run(self, status: str = "completed") -> int:
        with write_transaction(TEST_DATABASE_URL) as conn:
            row = conn.execute(
                "insert into node_runs(job_id, node_key, status) values (%s, 'generate', %s)"
                " returning id",
                (self.job["id"], status),
            ).fetchone()
        return int(row["id"])

    def set_sample_node_run(self, node_run_id: int) -> None:
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute(
                "update quality_sample_items set node_run_id=%s where id='item-1'",
                (node_run_id,),
            )

    def prove_execution(
        self,
        commit: str,
        ref: str,
        *,
        version_ref: str | None = None,
        node_run_id: int | None = None,
    ) -> None:
        run_id = self.node_run_id if node_run_id is None else node_run_id
        manifest = {
            "skill_commit": commit,
            "skill_version": f"{version_ref or ref}@{commit[:12]}",
        }
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute(
                "insert into agent_execution_requests("
                "execution_id, workspace_id, job_id, node_key, agent_id,"
                " agent_definition_hash, node_concurrency_limit, state, node_run_id,"
                " queued_at, finished_at, manifest_json)"
                " values (%s, %s, %s, 'generate', 'generate', '', 1, 'done', %s,"
                " current_timestamp, current_timestamp, %s)",
                (
                    f"proof-{run_id}",
                    self.workspace_id,
                    self.job["id"],
                    run_id,
                    json.dumps(manifest),
                ),
            )

    def prove_legacy_prefix(self, commit: str, ref: str, *, node_run_id: int | None = None) -> None:
        run_id = self.node_run_id if node_run_id is None else node_run_id
        with write_transaction(TEST_DATABASE_URL) as conn:
            conn.execute(
                "update node_runs set skill_version=%s where id=%s",
                (f"{ref}@{commit[:12]}", run_id),
            )

    def service(self) -> QualityReplayService:
        return QualityReplayService(self.job_db)

    def copy(self, replay: dict) -> tuple[dict, dict]:
        copy_job = self.job_db.get_job(str(replay["replay_job_id"]))
        run = self.job_db.get_run(str(copy_job["run_id"]))
        return copy_job, json.loads(str(run["frozen_pins_json"]))


def _copy_node(copy_job: dict) -> WorkflowNode:
    payload = json.loads(str(copy_job["workflow_definition_snapshot_json"]))
    return workflow_definition_from_dict(payload).nodes["generate"]


def _skill(ref: str = "latest") -> WorkflowNodeSkill:
    return WorkflowNodeSkill(key=_KEY, ref=ref)


def _real_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, SkillManager]:
    repo = _make_skill_repo(tmp_path / "skills", _KEY)
    manager = _make_manager(tmp_path)
    monkeypatch.setattr(skill_commit_pins, "build_skill_manager", lambda *_args: manager)
    return repo, manager


def _pool_one_db(job_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JobQueries:
    monkeypatch.setenv("AGENT_LEGION_DB_POOL_MAX_SIZE", "1")
    dsn = f"{job_db.dsn_identity}&application_name=replay-pool1-{uuid.uuid4().hex[:12]}"
    pool_db = JobQueries(dsn, tmp_path / "pool1-jobs")
    pool_for(dsn).timeout = 0.05
    return pool_db


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
    assert "skill_commits" not in pins


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


def test_archived_legacy_defaults_replay_with_materialized_mixed_profile(job_db) -> None:
    env = _Env(job_db, _definition(_ORIGINAL))
    revision_id = env.add_revision(
        2,
        _definition(WorkflowNodeExecution(provider="node-provider")),
        "archived",
        legacy_execution={
            "runtime": "velites",
            "provider": "top-provider",
            "model": "top-model",
            "thinking": "low",
        },
    )

    replay = env.service().create_replay(env.workspace_id, "item-1", revision_id=revision_id)

    copy_job, _pins = env.copy(replay)
    execution = _copy_node(copy_job).execution
    assert execution.runtime == "velites"
    assert execution.provider == "node-provider"
    assert execution.model == "top-model"
    assert execution.thinking == "low"


def test_frozen_legacy_job_snapshot_remains_replayable(job_db) -> None:
    env = _Env(
        job_db,
        _definition(None),
        legacy_execution={"runtime": "velites", "provider": "top-provider", "model": "top-model"},
    )

    replay = env.service().create_replay(env.workspace_id, "item-1")

    copy_job, _pins = env.copy(replay)
    execution = _copy_node(copy_job).execution
    assert execution.runtime == "velites"
    assert execution.provider == "top-provider"
    assert execution.model == "top-model"
    assert copy_job["workflow_definition_hash"] == env.job["workflow_definition_hash"]


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


def test_draft_choice_materializes_legacy_profile_and_preserves_overrides(job_db) -> None:
    env = _Env(job_db, _definition(_ORIGINAL))
    draft = {
        "key": "test",
        "label": "Test",
        "execution": {
            "runtime": "velites",
            "provider": "top-provider",
            "model": "top-model",
            "thinking": "low",
        },
        "nodes": {
            "intake": {"capability": "intake_items", "outputs": ["question.json"]},
            "generate": {
                "type": "agent",
                "capability": "write_script",
                "after": ["intake"],
                "inputs": ["question.json"],
                "outputs": ["key_info.json"],
                "execution": {"runtime": "pi", "provider": "node-provider"},
            },
        },
    }
    job_db.upsert_workspace_workflow_draft(env.workspace_id, yaml.safe_dump(draft))

    replay = env.service().create_replay(env.workspace_id, "item-1", use_draft=True)

    copy_job, _pins = env.copy(replay)
    execution = _copy_node(copy_job).execution
    assert execution.runtime == "pi"
    assert execution.provider == "node-provider"
    assert execution.model == "top-model"
    assert execution.thinking == "low"


def test_replay_profile_choice_errors(job_db, tmp_path: Path) -> None:
    env = _Env(job_db, _definition(_ORIGINAL))
    legacy_rev = env.add_revision(2, _definition(None), "archived")
    service = env.service()

    with pytest.raises(InvalidOperationError, match="retired.*revision_id"):
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
    assert "skill_commits" not in pins


def test_original_latest_freezes_executed_commit_after_head_moves_without_digest_drift(
    job_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, manager = _real_skill(tmp_path, monkeypatch)
    original = _head_commit(repo)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill()))
    env.prove_execution(original, "latest")
    _commit_skill_update(repo, "# newer HEAD\n")
    digest_payloads: list[dict] = []
    real_create_run = job_db.create_run

    def spy_create_run(*args, **kwargs):
        digest_payloads.append(args[2])
        return real_create_run(*args, **kwargs)

    monkeypatch.setattr(job_db, "create_run", spy_create_run)
    replay = env.service().create_replay(env.workspace_id, "item-1")

    _copy_job, pins = env.copy(replay)
    assert pins["skill_commits"]["generate"] == {
        "node_key": "generate",
        "skill_key": _KEY,
        "skill_ref": "latest",
        "skill_commit": original,
    }
    assert manager.load_lock().skills == {}
    assert len(digest_payloads) == 1
    assert "skill_commits" not in digest_payloads[0]
    assert set(digest_payloads[0]) == {
        "quality_replay",
        "node_config",
        "node_code_versions",
        "agent_versions",
        "node_profiles",
    }


def test_original_profile_freezes_the_sampled_run_not_the_latest_completed_run(
    job_db, tmp_path, monkeypatch
) -> None:
    repo, _manager = _real_skill(tmp_path, monkeypatch)
    sampled = _head_commit(repo)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill()))
    env.prove_execution(sampled, "latest")
    latest = _commit_skill_update(repo, "# newer execution and HEAD\n")
    latest_run = env.add_node_run()
    env.prove_execution(latest, "latest", node_run_id=latest_run)

    # The optional facade filter leaves the upgrade default (latest completed) intact.
    latest_identity = job_db.latest_done_request_identities(str(env.job["id"]), ["generate"])
    assert latest_identity["generate"][2] == latest
    assert (
        job_db.latest_done_request_identities("another-job", ["generate"], node_run_id=latest_run)
        == {}
    )

    replay = env.service().create_replay(env.workspace_id, "item-1")

    _copy_job, pins = env.copy(replay)
    assert pins["skill_commits"]["generate"]["skill_commit"] == sampled != latest
    sampled_identity = job_db.latest_done_request_identities(
        str(env.job["id"]), ["generate"], node_run_id=env.node_run_id
    )
    assert sampled_identity["generate"][2] == sampled


def test_original_pinned_ref_survives_retag_and_relock(job_db, tmp_path, monkeypatch) -> None:
    repo, manager = _real_skill(tmp_path, monkeypatch)
    original = _tag(repo, "v1")
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill("v1")))
    env.prove_execution(original, "v1")
    moved = _commit_skill_update(repo, "# retagged\n")
    assert _tag(repo, "v1", force=True) == moved
    assert skill_commit_pins.resolve_skill_commit(manager, _KEY, "v1") == moved

    replay = env.service().create_replay(env.workspace_id, "item-1")

    _copy_job, pins = env.copy(replay)
    assert pins["skill_commits"]["generate"]["skill_commit"] == original
    assert pins["skill_commits"]["generate"]["skill_ref"] == "v1"
    assert manager.load_lock().skills[_KEY].refs["v1"] == moved


def test_selected_revision_freezes_its_accept_time_head_not_the_original_commit(
    job_db, tmp_path, monkeypatch
) -> None:
    repo, manager = _real_skill(tmp_path, monkeypatch)
    original = _head_commit(repo)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill()))
    env.prove_execution(original, "latest")
    accepted = _commit_skill_update(repo, "# selected revision\n")
    revision_id = env.add_revision(2, _definition(_CANDIDATE, skill=_skill()), "active")

    replay = env.service().create_replay(env.workspace_id, "item-1", revision_id=revision_id)
    _commit_skill_update(repo, "# after acceptance\n")

    _copy_job, pins = env.copy(replay)
    pin = pins["skill_commits"]["generate"]
    assert pin["skill_commit"] == accepted != original
    assert pin["skill_ref"] == "latest"
    assert manager.load_lock().skills == {}


def test_selected_draft_freezes_its_accept_time_head(job_db, tmp_path, monkeypatch) -> None:
    repo, manager = _real_skill(tmp_path, monkeypatch)
    original = _head_commit(repo)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill()))
    env.prove_execution(original, "latest")
    accepted = _commit_skill_update(repo, "# selected draft\n")
    job_db.upsert_workspace_workflow_draft(
        env.workspace_id,
        yaml.safe_dump(
            {
                "key": "test",
                "label": "Test",
                "nodes": {
                    "generate": {
                        "type": "agent",
                        "capability": "write_script",
                        "inputs": ["question.json"],
                        "execution": {"runtime": "pi", "provider": "p3", "model": "m3"},
                        "skill": {"key": _KEY, "ref": "latest"},
                    }
                },
            }
        ),
    )

    replay = env.service().create_replay(env.workspace_id, "item-1", use_draft=True)
    _commit_skill_update(repo, "# draft moved after acceptance\n")

    _copy_job, pins = env.copy(replay)
    assert pins["skill_commits"]["generate"]["skill_commit"] == accepted != original
    assert manager.load_lock().skills == {}


def test_selected_unlocked_ref_uses_normal_first_pin_at_acceptance(
    job_db, tmp_path, monkeypatch
) -> None:
    repo, manager = _real_skill(tmp_path, monkeypatch)
    original = _head_commit(repo)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill()))
    env.prove_execution(original, "latest")
    selected = _tag(repo, "v2")
    revision_id = env.add_revision(2, _definition(_CANDIDATE, skill=_skill("v2")), "active")

    replay = env.service().create_replay(env.workspace_id, "item-1", revision_id=revision_id)

    _copy_job, pins = env.copy(replay)
    assert pins["skill_commits"]["generate"]["skill_commit"] == selected
    assert manager.load_lock().skills[_KEY].refs["v2"] == selected


def test_original_legacy_prefix_restores_unique_old_commit_not_current_head(
    job_db, tmp_path, monkeypatch
) -> None:
    repo, manager = _real_skill(tmp_path, monkeypatch)
    original = _head_commit(repo)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill()))
    env.prove_legacy_prefix(original, "latest")
    _commit_skill_update(repo, "# HEAD is not proof\n")

    replay = env.service().create_replay(env.workspace_id, "item-1")

    _copy_job, pins = env.copy(replay)
    assert pins["skill_commits"]["generate"]["skill_commit"] == original
    assert manager.load_lock().skills == {}


def test_noncompleted_sample_never_falls_back_to_the_latest_completed_run(
    job_db, tmp_path, monkeypatch
) -> None:
    repo, _manager = _real_skill(tmp_path, monkeypatch)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill()))
    env.prove_execution(_head_commit(repo), "latest")
    failed_run = env.add_node_run("failed")
    env.set_sample_node_run(failed_run)

    assert (
        job_db.latest_done_request_identities(
            str(env.job["id"]), ["generate"], node_run_id=failed_run
        )
        == {}
    )
    with pytest.raises(InvalidOperationError, match="sampled node_run.*no completed"):
        env.service().create_replay(env.workspace_id, "item-1")

    assert env.service().list_replays(env.workspace_id, "item-1") == []
    assert job_db.list_runs(env.workspace_id) == []


def test_original_without_execution_proof_fails_before_any_replay_write(
    job_db, tmp_path, monkeypatch
) -> None:
    _repo, _manager = _real_skill(tmp_path, monkeypatch)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill()))

    with pytest.raises(InvalidOperationError, match="cannot prove.*neither a full"):
        env.service().create_replay(env.workspace_id, "item-1")

    assert env.service().list_replays(env.workspace_id, "item-1") == []
    assert job_db.list_runs(env.workspace_id) == []


def test_original_proof_ref_mismatch_fails_closed(job_db, tmp_path, monkeypatch) -> None:
    repo, _manager = _real_skill(tmp_path, monkeypatch)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill("v1")))
    env.prove_execution(_head_commit(repo), "v1", version_ref="other")

    with pytest.raises(InvalidOperationError, match="does not match ref 'v1'"):
        env.service().create_replay(env.workspace_id, "item-1")

    assert job_db.list_runs(env.workspace_id) == []


def test_ambiguous_legacy_prefix_fails_closed_instead_of_using_head(
    job_db, tmp_path, monkeypatch
) -> None:
    repo, manager = _real_skill(tmp_path, monkeypatch)
    original = _head_commit(repo)
    env = _Env(job_db, _definition(_ORIGINAL, skill=_skill()))
    env.prove_legacy_prefix(original, "latest")

    def ambiguous(*_args):
        raise SkillRepoError("short object id is ambiguous")

    monkeypatch.setattr(manager, "_rev_parse", ambiguous)
    with pytest.raises(InvalidOperationError, match="does not resolve uniquely"):
        env.service().create_replay(env.workspace_id, "item-1")

    assert job_db.list_runs(env.workspace_id) == []


def test_original_profile_create_replay_needs_only_one_pool_connection(
    job_db, tmp_path, monkeypatch
) -> None:
    pool_db = _pool_one_db(job_db, tmp_path, monkeypatch)
    repo, _manager = _real_skill(tmp_path, monkeypatch)
    original = _head_commit(repo)
    env = _Env(pool_db, _definition(_ORIGINAL, skill=_skill()))
    env.prove_execution(original, "latest")

    replay = env.service().create_replay(env.workspace_id, "item-1")

    _copy_job, pins = env.copy(replay)
    assert pins["skill_commits"]["generate"]["skill_commit"] == original
    with pytest.raises(ConflictError, match="already in progress"):
        env.service().create_replay(env.workspace_id, "item-1")
    assert len(pool_db.list_runs(env.workspace_id)) == 1


@pytest.mark.parametrize("use_draft", [False, True], ids=["revision", "draft"])
def test_selected_profile_create_replay_needs_only_one_pool_connection(
    job_db, tmp_path, monkeypatch, use_draft
) -> None:
    pool_db = _pool_one_db(job_db, tmp_path, monkeypatch)
    repo, _manager = _real_skill(tmp_path, monkeypatch)
    accepted = _head_commit(repo)
    env = _Env(pool_db, _definition(_ORIGINAL, skill=_skill()))
    revision_id = None
    if use_draft:
        pool_db.upsert_workspace_workflow_draft(
            env.workspace_id,
            yaml.safe_dump(
                {
                    "key": "test",
                    "label": "Test",
                    "nodes": {
                        "generate": {
                            "type": "agent",
                            "capability": "write_script",
                            "inputs": ["question.json"],
                            "execution": {"runtime": "pi", "provider": "p3", "model": "m3"},
                            "skill": {"key": _KEY, "ref": "latest"},
                        }
                    },
                }
            ),
        )
    else:
        revision_id = env.add_revision(2, _definition(_CANDIDATE, skill=_skill()), "active")

    replay = env.service().create_replay(
        env.workspace_id, "item-1", revision_id=revision_id, use_draft=use_draft
    )

    _copy_job, pins = env.copy(replay)
    assert pins["skill_commits"]["generate"]["skill_commit"] == accepted
