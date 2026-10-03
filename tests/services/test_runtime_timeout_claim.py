"""#691: Worker claim re-resolves the runtime-adjustable ``timeout_seconds``.

A request enqueued for a remote Worker (agent or code) can sit in the queue;
a workspace override changed meanwhile must reach the claimed manifest, while
an execution that was already claimed keeps the value it started with. The
per-run audit (``node_runs.config_snapshot_json``) records value + source.
"""

from __future__ import annotations

import json

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.runtime_reserved_config import CONFIG_RESOLUTION_AUDIT_KEY
from tests.helpers import replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL

WORKSPACE = "test-workspace"
MODEL = "test-model"


def _seed(job_db, *, job_id: str, node_key: str, node_config: dict, route_agent: bool) -> None:
    revision = {
        "nodes": {
            node_key: {
                "key": node_key,
                "config": node_config,
                "execution": {"provider": "gateway", "model": MODEL},
            }
        }
    }
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, 'Test', %s)"
            " on conflict(id) do nothing",
            (WORKSPACE, WORKSPACE),
        )
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, version, status, definition_json,"
            " definition_hash) values ('rev-1', %s, 1, 'active', %s, 'h1')"
            " on conflict(id) do nothing",
            (WORKSPACE, json.dumps(revision)),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, workflow_revision_id)"
            " values (%s, %s, 'question', %s, 'rev-1')",
            (job_id, WORKSPACE, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))
        if route_agent:
            conn.execute(
                "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
                " values (%s, %s, 'agent', 'generator-v1') on conflict do nothing",
                (WORKSPACE, node_key),
            )


def _set_override(job_db, node_key: str, override: dict | None) -> None:
    payload = {WORKSPACE: {node_key: override}} if override is not None else {}
    with job_db.connect() as conn:
        conn.execute(
            "update workspaces set node_config_json=%s where id=%s",
            (json.dumps(payload), WORKSPACE),
        )


def _register_worker() -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        capabilities=["generate", "package"],
        models=[{"provider": "gateway", "model": MODEL}],
        max_concurrency=10,
        max_code_concurrency=2,
        labels={},
        protocol_version=2,
    )


def _enqueue_agent(broker: AgentExecutionBroker, job_id: str) -> None:
    definition = AgentDefinition(capability="generate", runtime="pi", skill="question/generate")
    replace_agent_catalog(WORKSPACE, {"generator-v1": definition})
    queued = broker.enqueue(
        AgentExecutionRequest(
            workspace_id=WORKSPACE,
            job_id=job_id,
            workflow_key=WORKSPACE,
            node_key="generate",
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            manifest={
                "job_id": job_id,
                "workflow_key": WORKSPACE,
                "log_path": f"logs/{job_id}.log",
                "runtime": "pi",
                # Enqueue-time (dispatch) resolution: no override yet.
                "execution": {"provider": "gateway", "model": MODEL, "timeout_seconds": 1800},
                "config_resolution": {
                    "timeout_seconds": {"value": 1800, "source": "platform_default"}
                },
            },
        )
    )
    assert queued is not None


def _enqueue_code(broker: AgentExecutionBroker, job_id: str) -> None:
    queued = broker.enqueue(
        AgentExecutionRequest(
            workspace_id=WORKSPACE,
            job_id=job_id,
            workflow_key=WORKSPACE,
            node_key="package",
            agent_id="package",
            agent_definition_hash="codehash",
            manifest={
                "kind": "code",
                "capability": "package",
                "code_hash": "abc123",
                "job_id": job_id,
                "workflow_key": WORKSPACE,
                "log_path": f"logs/{job_id}.log",
                "config": {"mode": "fast", "timeout_seconds": 600},
                "timeout_seconds": 600,
                "sandbox_network": False,
            },
            kind="code",
        )
    )
    assert queued is not None


def _snapshot(job_db, job_id: str) -> dict:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select config_snapshot_json from node_runs where job_id=%s", (job_id,)
        ).fetchone()
    assert row is not None
    return json.loads(row["config_snapshot_json"])


def test_queued_agent_request_picks_up_override_changed_after_dispatch(job_db) -> None:
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    _seed(job_db, job_id="job-a", node_key="generate", node_config={}, route_agent=True)
    _enqueue_agent(broker, "job-a")
    _register_worker()
    _set_override(job_db, "generate", {"timeout_seconds": 7200})

    claimed = broker.claim("worker-1")

    assert claimed is not None and claimed.kind == "agent"
    assert claimed.manifest["execution"]["timeout_seconds"] == 7200
    audit = _snapshot(job_db, "job-a")[CONFIG_RESOLUTION_AUDIT_KEY]
    assert audit == {"timeout_seconds": {"value": 7200, "source": "workspace_override"}}


def test_queued_code_request_picks_up_override_changed_after_dispatch(job_db) -> None:
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    _seed(job_db, job_id="job-c", node_key="package", node_config={}, route_agent=False)
    _enqueue_code(broker, "job-c")
    _register_worker()
    _set_override(job_db, "package", {"timeout_seconds": 1500})

    claimed = broker.claim("worker-1")

    assert claimed is not None and claimed.kind == "code"
    assert claimed.manifest["timeout_seconds"] == 1500
    assert claimed.manifest["config"]["timeout_seconds"] == 1500
    snapshot = _snapshot(job_db, "job-c")
    assert snapshot["mode"] == "fast"
    assert snapshot[CONFIG_RESOLUTION_AUDIT_KEY] == {
        "timeout_seconds": {"value": 1500, "source": "workspace_override"}
    }


def test_claim_precedence_node_config_then_platform_default(job_db) -> None:
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    # Node config layer (from the job's revision) wins without an override.
    _seed(
        job_db,
        job_id="job-n",
        node_key="package",
        node_config={"timeout_seconds": 900},
        route_agent=False,
    )
    _enqueue_code(broker, "job-n")
    _register_worker()
    claimed = broker.claim("worker-1")
    assert claimed is not None
    assert claimed.manifest["timeout_seconds"] == 900
    assert _snapshot(job_db, "job-n")[CONFIG_RESOLUTION_AUDIT_KEY]["timeout_seconds"] == {
        "value": 900,
        "source": "node_config",
    }


def test_agent_claim_falls_back_to_agent_platform_default(job_db) -> None:
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    _seed(job_db, job_id="job-d", node_key="generate", node_config={}, route_agent=True)
    _enqueue_agent(broker, "job-d")
    _register_worker()
    _set_override(job_db, "generate", {"sandbox_network": True})  # unrelated key

    claimed = broker.claim("worker-1")

    assert claimed is not None
    assert claimed.manifest["execution"]["timeout_seconds"] == 1800
    assert _snapshot(job_db, "job-d")[CONFIG_RESOLUTION_AUDIT_KEY]["timeout_seconds"] == {
        "value": 1800,
        "source": "platform_default",
    }


def test_claimed_execution_is_unaffected_by_a_later_override_change(job_db) -> None:
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    _seed(job_db, job_id="job-r", node_key="package", node_config={}, route_agent=False)
    _enqueue_code(broker, "job-r")
    _register_worker()
    _set_override(job_db, "package", {"timeout_seconds": 1200})
    claimed = broker.claim("worker-1")
    assert claimed is not None and claimed.manifest["timeout_seconds"] == 1200

    # Raising the knob after the claim does not touch the running execution:
    # no re-claim, the run's audit keeps the value it started with.
    _set_override(job_db, "package", {"timeout_seconds": 9999})
    assert broker.claim("worker-1") is None
    with job_db._connect_read() as conn:
        state = conn.execute(
            "select state from agent_execution_requests where job_id='job-r'"
        ).fetchone()
    assert state["state"] == "claimed"
    assert _snapshot(job_db, "job-r")[CONFIG_RESOLUTION_AUDIT_KEY]["timeout_seconds"] == {
        "value": 1200,
        "source": "workspace_override",
    }
