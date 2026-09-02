"""Terminal-state manifest slimming for kind='agent' rows (issue #354).

The agent-kind twin of ``test_code_manifest_trim.py`` (#142): the queued
agent manifest (prompt, config, inputs, command_spec, input_artifact refs;
2-10KB) is replaced by a small audit stub at every terminal transition —
``mark_done`` / ``cancel_request`` / the requeue-limit-exceeded sweep /
agent-disabled and unclaimable sweeps / the rerun-path queued cancel. The
stub keeps the identity skeleton (execution/job/workspace/node/agent ids,
capability, log_path, skill pins) so post-terminal readers (quality sampling
reads ``capability``; the bundle reaper reads ``execution_id``) keep working.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.claim import cancel_request
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import write_transaction
from tests.helpers import replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL

_WORKSPACE = "test-workspace"
_AGENT_ID = "generator-v1"

# The heavy manifest dispatch.py builds (subset of keys; the assertions only
# care that the heavy halves disappear and the stub skeleton stays).
_FULL_AGENT_MANIFEST = {
    "execution_id": "",  # filled per enqueue
    "workspace_id": _WORKSPACE,
    "job_id": "job-1",
    "workflow_key": "questions",
    "node_key": "generate",
    "node_label": "Generate",
    "agent_id": _AGENT_ID,
    "agent_definition_hash": "",  # filled per enqueue
    "runtime": "pi",
    "capability": "generate",
    "inputs": ["question.md"],
    "expected_outputs": ["answer.json"],
    "additional_prompt": "Answer carefully",
    "config": {"page_size": 10},
    "tools": ["read", "write"],
    "log_path": "logs/job-1.log",
    "execution": {"provider": "gateway", "model": "test-model"},
    "command_spec": {"prompt": "BIG PROMPT " * 200, "command": ["pi"], "version": 1},
    "input_artifacts": {"question.md": "sha256:" + "a" * 64},
    "skill": "question/generate",
    "skill_ref": "v1",
    "skill_version": "v1@aaaaaaaaaaaa",
    "skill_commit": "b" * 40,
    "agent_version": 3,
}

_STUB_KEYS = {
    "execution_id",
    "job_id",
    "workspace_id",
    "node_key",
    "agent_id",
    "capability",
    "runtime",
    "log_path",
    "skill",
    "skill_version",
    "skill_commit",
    "agent_version",
    "trimmed",
}


def _publish_agent() -> str:
    definition = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    replace_agent_catalog(_WORKSPACE, {_AGENT_ID: definition})
    return definition.definition_hash()


def _insert_agent_job_rows(job_db, *, job_id: str = "job-1") -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, 'Test', 'demo_workflow') on conflict(id) do nothing",
            (_WORKSPACE,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values (%s, %s, 'question', %s)",
            (job_id, _WORKSPACE, job_id),
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key) values (%s, 'generate')",
            (job_id,),
        )
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
            " values (%s, 'generate', 'agent', %s)"
            " on conflict(workspace_id, node_key) do nothing",
            (_WORKSPACE, _AGENT_ID),
        )


def _enqueue_agent(broker: AgentExecutionBroker, *, manifest: dict) -> str:
    execution_id = broker.enqueue(
        AgentExecutionRequest(
            workspace_id=_WORKSPACE,
            job_id="job-1",
            workflow_key="questions",
            node_key="generate",
            agent_id=_AGENT_ID,
            agent_definition_hash=manifest["agent_definition_hash"],
            manifest=manifest,
        )
    )
    assert execution_id is not None
    return execution_id


def _full_manifest(definition_hash: str, execution_id: str = "") -> dict:
    manifest = dict(_FULL_AGENT_MANIFEST)
    manifest["agent_definition_hash"] = definition_hash
    manifest["execution_id"] = execution_id
    return manifest


def _register_agent_worker() -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-agent",
        name="worker",
        runtimes=["pi"],
        models=[{"provider": "gateway", "model": "test-model"}],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )


def _stored_manifest(job_db, execution_id: str) -> dict:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select manifest_json from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    return json.loads(row["manifest_json"])


def _assert_stubbed(manifest: dict, execution_id: str) -> None:
    assert set(manifest) == _STUB_KEYS
    assert manifest["execution_id"] == execution_id
    assert manifest["job_id"] == "job-1"
    assert manifest["workspace_id"] == _WORKSPACE
    assert manifest["node_key"] == "generate"
    assert manifest["agent_id"] == _AGENT_ID
    assert manifest["capability"] == "generate"
    assert manifest["log_path"] == "logs/job-1.log"
    assert manifest["skill"] == "question/generate"
    assert manifest["skill_commit"] == "b" * 40
    # ``->>`` extraction renders every stub value as text; agent_version is
    # an audit reference, not a typed field.
    assert manifest["agent_version"] == "3"
    assert manifest["trimmed"] is True
    # The heavy halves are gone — the whole point of issue #354.
    assert "command_spec" not in manifest
    assert "inputs" not in manifest
    assert "input_artifacts" not in manifest
    assert "config" not in manifest
    assert "additional_prompt" not in manifest


def test_mark_done_slims_agent_manifest(job_db, tmp_path) -> None:
    definition_hash = _publish_agent()
    _insert_agent_job_rows(job_db)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=tmp_path, lease_ttl_seconds=1)
    execution_id = _enqueue_agent(broker, manifest=_full_manifest(definition_hash))
    _register_agent_worker()

    claimed = broker.claim("worker-agent")
    assert claimed is not None
    # The claim response still carries the full manifest (queued copy; the
    # claim path re-renders command_spec live) until the terminal transition
    # slims it.
    assert claimed.manifest["command_spec"]["command"]
    assert claimed.manifest["inputs"] == ["question.md"]

    broker.mark_done(
        claimed.execution_id, "worker-agent", claimed.lease_id, {"status": "completed"}
    )

    _assert_stubbed(_stored_manifest(job_db, execution_id), execution_id)


def test_cancel_request_slims_agent_manifest(job_db, tmp_path) -> None:
    definition_hash = _publish_agent()
    _insert_agent_job_rows(job_db)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=tmp_path)
    execution_id = _enqueue_agent(broker, manifest=_full_manifest(definition_hash))

    with write_transaction(TEST_DATABASE_URL) as conn:
        cancel_request(conn, execution_id)

    _assert_stubbed(_stored_manifest(job_db, execution_id), execution_id)


def test_sweep_requeue_limit_exceeded_slims_agent_manifest(job_db, tmp_path) -> None:
    definition_hash = _publish_agent()
    _insert_agent_job_rows(job_db)
    broker = AgentExecutionBroker(
        TEST_DATABASE_URL, data_dir=tmp_path, lease_ttl_seconds=1, requeue_limit=0
    )
    execution_id = _enqueue_agent(broker, manifest=_full_manifest(definition_hash))
    _register_agent_worker()
    claimed = broker.claim("worker-agent")
    assert claimed is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=10), execution_id),
        )

    assert broker.sweep_expired_claims() == []

    _assert_stubbed(_stored_manifest(job_db, execution_id), execution_id)


def test_mark_done_on_stubbed_manifest_is_idempotent(job_db, tmp_path) -> None:
    """A trimmed manifest passes through a later terminal write unchanged.

    Terminal transitions cannot legally re-fire (mark_done is bounded to
    claimed/reporting state), but the trim SQL itself is applied on every
    terminal write, so it must be a no-op on already-trimmed rows — the
    stub rebuilds to itself because it only reads stub keys."""
    definition_hash = _publish_agent()
    _insert_agent_job_rows(job_db)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=tmp_path, lease_ttl_seconds=1)
    execution_id = _enqueue_agent(broker, manifest=_full_manifest(definition_hash))
    _register_agent_worker()

    with write_transaction(TEST_DATABASE_URL) as conn:
        cancel_request(conn, execution_id)
    first = _stored_manifest(job_db, execution_id)
    _assert_stubbed(first, execution_id)

    # A second terminal-path write on the same row (defensive): the stub is
    # rebuilt from stub keys only, so the result is identical.
    with write_transaction(TEST_DATABASE_URL) as conn:
        cancel_request(conn, execution_id)
    assert _stored_manifest(job_db, execution_id) == first
