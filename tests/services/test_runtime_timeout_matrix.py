"""CONFIG-RUNTIME-TIMEOUT-001 test matrix (#691): one model, every path.

The model (``server/app/services/runtime_reserved_config.py``) is restated
below as a tiny pure oracle — the spec in code — and every combination of
kind × path × layer state × change timing drives the REAL code path against
PostgreSQL and must agree with it on the effective timeout, the agent
command spec ``--timeout-seconds``, the code manifest top-level timeout (+
its ``config`` copy) and the audit (value + source); ``sandbox_network``
must always equal the intake-frozen value.

Pruned combinations (explicit, see ``_cases``):

- agent × local dispatch: agent nodes only run on remote Workers.
- local dispatch × ``after_enqueue``: the local code pool never enqueues;
  its dispatch is the decision point.
- legacy × ``before_intake`` / ``after_intake``: a legacy request is one a
  pre-#691 Host already put in the queue (no ``timeout_base``); only changes
  after that enqueue can still be observed.
- legacy × ``L1`` / ``L1+L2``: the legacy base is the enqueue-time value, the
  node layer is already folded into it — identical to ``L0`` / ``L2``.

Coverage split: the full matrix runs through PostgreSQL here (no pure-only
remainder); the job never pins a revision, proving the claim never needs the
revision document for the timeout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker import dispatch as agent_dispatch
from server.app.agent_broker.claim_batch import claim_batch
from server.app.agent_broker.claim_scan import fetch_candidates
from server.app.agent_broker.claim_timeout import WORKSPACE_TIMEOUT_COLUMN
from server.app.agent_broker.code_dispatch import CodeDispatchService
from server.app.agent_broker.code_manifest_config import split_manifest_config
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import read_connection
from server.app.executors.contracts import CodeCapabilityConfig
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactStore
from server.app.services.node_codes import NodeCodeService
from server.app.services.node_config import (
    dispatch_config_resolution,
    resolve_workflow_node_configs,
)
from server.app.services.node_config_batch import run_frozen_payload
from server.app.services.node_execution_config import (
    agent_effective_schema,
    merge_reserved_execution_schema,
    node_config_reserved_defaults,
)
from server.app.settings import Settings
from server.app.skills.checkout import SkillCheckout
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode
from server.app.workflows.schema import WorkflowNodeExecution
from tests.helpers import replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker

REPO_ROOT = Path(__file__).resolve().parents[2]
WS = "test"  # workspace id == workflow key (DB-WORKSPACE-KEY-BINDING-001)
NODE = {"agent": "generate", "code": "package"}
DEFAULT = {"agent": 1800, "code": 600}
LEGACY_ENQUEUED = 1111  # what a pre-#691 Host baked into the queued manifest

# layer state -> (L1 node config timeout, L2 workspace override timeout)
LAYERS: dict[str, tuple[Any, Any]] = {
    "L0": (None, None),
    "L1": (900, None),
    "L2": (None, 2400),
    "L1+L2": (900, 2400),
    "invalid_L2": (900, "soon"),
}
TIMINGS = ("before_intake", "after_intake", "after_enqueue", "after_decision")


# --- the oracle: the model restated -----------------------------------------


def _valid(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def oracle(kind: str, l1: Any, l2: Any, *, legacy: bool = False) -> dict[str, Any]:
    if legacy:
        base = {"value": LEGACY_ENQUEUED, "source": "enqueue_snapshot"}
    elif _valid(l1):
        base = {"value": l1, "source": "node_config"}
    else:
        base = {"value": DEFAULT[kind], "source": "platform_default"}
    if l2 is None:
        return base
    if _valid(l2):
        return {"value": l2, "source": "workspace_override"}
    return {"value": base["value"], "source": "workspace_override_invalid"}


def _cases() -> list[tuple[str, str, str, str]]:
    cases = []
    for kind in ("agent", "code"):
        for path in ("local", "single", "batch", "legacy"):
            if kind == "agent" and path == "local":
                continue  # agent nodes never run in the local code pool
            for layer in LAYERS:
                if path == "legacy" and layer in ("L1", "L1+L2"):
                    continue  # legacy base already folds L1 in
                for timing in TIMINGS:
                    if path == "local" and timing == "after_enqueue":
                        continue  # no enqueue on the local path
                    if path == "legacy" and timing in ("before_intake", "after_intake"):
                        continue  # legacy requests predate the change
                    cases.append((kind, path, layer, timing))
    return cases


# --- fixtures through the real paths -----------------------------------------


def _node(kind: str, l1: Any) -> WorkflowNode:
    config = {} if l1 is None else {"timeout_seconds": l1}
    if kind == "agent":
        return WorkflowNode(
            key=NODE["agent"],
            label="Generate",
            capability="generate",
            node_type="agent",
            config=config,
            outputs=["answer.json"],
            execution=WorkflowNodeExecution(provider="gw", model="m"),
        )
    return WorkflowNode(
        key=NODE["code"],
        label="Package",
        capability="package",
        config=config,
        config_schema={"properties": {"mode": {"type": "string", "default": "fast"}}},
        outputs=["out.json"],
    )


def _agent_definition() -> AgentDefinition:
    return AgentDefinition(capability="generate", runtime="velites", skill="question/generate")


def _set_override(job_db: JobQueries, kind: str, l2: Any) -> None:
    # Every change also tries to open the network: it must never reach a job
    # intaken before the change (sandbox_network stays intake-frozen).
    values: dict[str, Any] = {"sandbox_network": True}
    if l2 is not None:
        values["timeout_seconds"] = l2
    job_db.update_workspace(WS, node_config={WS: {NODE[kind]: values}})


def _intake(job_db: JobQueries, kind: str, node: WorkflowNode, job_id: str) -> dict:
    definition = WorkflowDefinition(
        key=WS, label="T", intake=WorkflowIntake(), nodes={node.key: node}
    )
    frozen = resolve_workflow_node_configs(
        definition, {"generator": _agent_definition()}, job_db.get_workspace(WS)
    )
    run = job_db.create_run(WS, "batch_by_ids", {"node_config": frozen}, WS)
    job = job_db.create_job(
        workflow_key=WS,
        source_type="question",
        source_id=job_id,
        run_id=str(run["id"]),
        title=job_id,
        node_keys=[node.key],
        workspace_id=WS,
    )
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set frozen_config_json=%s where id=%s",
            (json.dumps({node.key: frozen[node.key]}), job["id"]),
        )
    return job_db.get_job(job["id"])


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        root_dir=REPO_ROOT,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
        database_url=TEST_DATABASE_URL,
    )


def _broker(tmp_path: Path) -> AgentExecutionBroker:
    return AgentExecutionBroker(
        TEST_DATABASE_URL, data_dir=tmp_path, bundle_dir=tmp_path / "bundles"
    )


def _enqueue(job_db, tmp_path, monkeypatch, kind: str, node: WorkflowNode, job: dict) -> None:
    """The Host enqueue: the same calls code_claim / agent_claim make."""
    workspace = job_db.get_workspace(WS)
    payload = run_frozen_payload(job_db, job)
    defaults = node_config_reserved_defaults(node.config)
    broker = _broker(tmp_path)
    if kind == "code":
        schema = merge_reserved_execution_schema(node.config_schema)
        unresolved, base = dispatch_config_resolution(
            schema, node, WS, workspace, payload, defaults, decide=False
        )
        config, secret_config = split_manifest_config(schema, unresolved)
        service = CodeDispatchService(
            _settings(tmp_path),
            broker,
            ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL),
            job_db,
        )
        assert service.enqueue(
            capability=node.capability,
            capability_config=CodeCapabilityConfig(
                config_schema=schema,
                timeout_seconds=unresolved["timeout_seconds"],
                sandbox_network=unresolved["sandbox_network"],
            ),
            workspace=workspace,
            job=job,
            workflow_key=WS,
            node=node,
            job_dir=tmp_path / "jobs" / job["id"],
            log_path=tmp_path / "logs" / f"{job['id']}.log",
            inputs=(),
            code_text="def run(ctx):\n    pass\n",
            custom_code=True,
            config=config,
            secret_config=secret_config,
            timeout_base=base,
        )
        return
    definition = _agent_definition()
    node_config, base = dispatch_config_resolution(
        agent_effective_schema(definition.config_schema),
        node,
        WS,
        workspace,
        payload,
        defaults,
        decide=False,
    )
    checkout = SkillCheckout(
        key="question/generate", ref="v1", run_dir=tmp_path, commit="c" * 40, version="v1@cccc"
    )
    monkeypatch.setattr(agent_dispatch, "build_skill_manager", lambda *_a: MagicMock())
    monkeypatch.setattr(agent_dispatch, "AgentEnqueuePool", lambda **_k: MagicMock())
    monkeypatch.setattr(agent_dispatch, "checkout_node_skill", lambda *_a: checkout)
    monkeypatch.setattr(agent_dispatch, "stage_agent_inputs", lambda *_a: None)
    monkeypatch.setattr(agent_dispatch, "build_agent_bundle", lambda *_a, **_k: None)
    service = agent_dispatch.AgentDispatchService(_settings(tmp_path), broker, MagicMock())
    assert service.enqueue(
        agent_id="generator",
        definition=definition,
        workspace=workspace,
        job=job,
        workflow_key=WS,
        node=node,
        job_dir=tmp_path / "jobs" / job["id"],
        log_path=tmp_path / "logs" / f"{job['id']}.log",
        inputs=(),
        node_config=node_config,
        timeout_base=base,
    )


def _make_legacy(job_db: JobQueries, kind: str) -> None:
    """Rewrite the queued manifest into the pre-#691 shape (no base)."""
    with job_db.connect() as conn:
        row = conn.execute(
            "select execution_id, manifest_json from agent_execution_requests"
        ).fetchone()
        manifest = json.loads(row["manifest_json"])
        manifest.pop("timeout_base")
        if kind == "code":
            manifest["timeout_seconds"] = LEGACY_ENQUEUED
            manifest["config"]["timeout_seconds"] = LEGACY_ENQUEUED
        else:
            manifest["execution"]["timeout_seconds"] = LEGACY_ENQUEUED
        conn.execute(
            "update agent_execution_requests set manifest_json=%s where execution_id=%s",
            (json.dumps(manifest), row["execution_id"]),
        )


def _seed_workspace(job_db: JobQueries, kind: str) -> None:
    job_db.create_workspace("WS", default_workflow_key=WS, workspace_id=WS)
    if kind == "agent":
        replace_agent_catalog(WS, {"generator": _agent_definition()})
        with job_db.connect() as conn:
            conn.execute(
                "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
                " values (%s, %s, 'agent', 'generator')",
                (WS, NODE["agent"]),
            )
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["velites"],
        capabilities=["generate", "package"],
        models=[{"runtime": "velites", "provider": "gw", "model": "m"}],
        max_concurrency=10,
        max_code_concurrency=4,
        labels={},
        protocol_version=3,
    )


def _audit(job_db: JobQueries, job_id: str) -> dict:
    with job_db._connect_read() as conn:
        rows = conn.execute(
            "select config_snapshot_json from node_runs where job_id=%s", (job_id,)
        ).fetchall()
    assert len(rows) == 1, "exactly one decided execution"
    return json.loads(rows[0]["config_snapshot_json"])["_config_resolution"]["timeout_seconds"]


def _claim(tmp_path: Path, path: str):
    broker = _broker(tmp_path)
    if path == "batch":
        claims = claim_batch(broker, "worker-1", None, None, limit=4)
        return claims[0] if claims else None
    return broker.claim("worker-1")


def _run_remote(job_db, tmp_path, monkeypatch, kind, path, layer, timing) -> dict[str, Any]:
    l1, l2 = LAYERS[layer]
    node = _node(kind, l1)
    _seed_workspace(job_db, kind)
    if timing == "before_intake":
        _set_override(job_db, kind, l2)
    job = _intake(job_db, kind, node, "job-1")
    if timing == "after_intake":
        _set_override(job_db, kind, l2)
    _enqueue(job_db, tmp_path, monkeypatch, kind, node, job)
    if path == "legacy":
        _make_legacy(job_db, kind)
    if timing == "after_enqueue":
        _set_override(job_db, kind, l2)
    claimed = _claim(tmp_path, path)
    assert claimed is not None and claimed.kind == kind
    manifest = claimed.manifest
    if kind == "code":
        effective = manifest["timeout_seconds"]
        assert manifest["config"]["timeout_seconds"] == effective
        sandbox = manifest["sandbox_network"]
    else:
        effective = manifest["execution"]["timeout_seconds"]
        command = manifest["command_spec"]["command"]
        assert command[command.index("--timeout-seconds") + 1] == str(effective)
        sandbox = None  # agent runtimes ignore sandbox_network (inert)
    decided = manifest["config_resolution"]["timeout_seconds"]
    if timing == "after_decision":
        _set_override(job_db, kind, l2)
        assert _claim(tmp_path, path) is None  # no re-decision
    return {
        "effective": effective,
        "decided": decided,
        "audit": _audit(job_db, job["id"]),
        "sandbox": sandbox,
    }


def _run_local(job_db, tmp_path, layer, timing) -> dict[str, Any]:
    l1, l2 = LAYERS[layer]
    node = _node("code", l1)
    _seed_workspace(job_db, "code")
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(WS, WS, node.key, "def run(job, job_dir, runtime):\n    pass\n", "seed")
    codes.publish(WS, WS, node.key)
    if timing == "before_intake":
        _set_override(job_db, "code", l2)
    job = _intake(job_db, "code", node, "job-1")
    if timing == "after_intake":
        _set_override(job_db, "code", l2)
    executor = RecordingExecutor("code")
    definition = WorkflowDefinition(
        key=WS, label="T", intake=WorkflowIntake(), nodes={node.key: node}
    )
    worker = _make_worker(tmp_path, TEST_DATABASE_URL, executor, [definition])
    try:
        assert worker._poll() is True  # dispatch = the decision; executor blocks
        if timing == "after_decision":
            _set_override(job_db, "code", l2)
        executor.block_event.set()
        for future in list(worker.state.futures.values()):
            future.result(timeout=5)
        worker._poll()
    finally:
        worker.stop()
    assert len(executor.contexts) == 1, "a running execution is never re-decided"
    config = executor.contexts[0].node_config
    audit = _audit(job_db, job["id"])
    return {
        "effective": config["timeout_seconds"],
        "decided": audit,
        "audit": audit,
        "sandbox": config["sandbox_network"],
    }


def _drive(job_db, tmp_path, monkeypatch, kind, path, layer, timing) -> dict[str, Any]:
    if path == "local":
        return _run_local(job_db, tmp_path, layer, timing)
    return _run_remote(job_db, tmp_path, monkeypatch, kind, path, layer, timing)


# --- the matrix ---------------------------------------------------------------


@pytest.mark.parametrize(("kind", "path", "layer", "timing"), _cases())
def test_timeout_matrix(job_db, tmp_path, monkeypatch, kind, path, layer, timing) -> None:
    l1, l2 = LAYERS[layer]
    l2_at_decision = None if timing == "after_decision" else l2
    expected = oracle(kind, l1, l2_at_decision, legacy=path == "legacy")

    result = _drive(job_db, tmp_path, monkeypatch, kind, path, layer, timing)

    assert result["effective"] == expected["value"]
    assert result["decided"] == expected
    assert result["audit"] == expected  # the audit records exactly the decision
    if result["sandbox"] is not None:
        # sandbox_network is whatever the intake froze: only an override
        # written before intake can carry the network opt-in.
        assert result["sandbox"] is (timing == "before_intake")


@pytest.mark.parametrize("layer", list(LAYERS))
def test_paths_agree_for_identical_inputs(job_db, tmp_path, monkeypatch, layer) -> None:
    """Local dispatch, single claim and batch claim decide identically."""
    results = []
    for path in ("local", "single", "batch"):
        # Fresh workspace per path (cascades its jobs/requests/node code).
        with job_db.connect() as conn:
            conn.execute("delete from workspaces where id=%s", (WS,))
            conn.execute("delete from agent_workers")
        timing = "after_intake" if path == "local" else "after_enqueue"
        results.append(
            _drive(job_db, tmp_path, monkeypatch, "code", path, layer, timing)["decided"]
        )
    assert results[0] == results[1] == results[2] == oracle("code", *LAYERS[layer])


def test_claim_scan_projects_a_scalar_override_not_the_document(
    job_db, tmp_path, monkeypatch
) -> None:
    """Structural guard (codex P2 on #862): no whole workspace config document
    rides the candidate rows; L2 arrives as a scalar, and a non-scalar override
    collapses to an invalid marker (→ base, ``workspace_override_invalid``)."""
    node = _node("code", None)
    _seed_workspace(job_db, "code")
    job = _intake(job_db, "code", node, "job-1")
    _enqueue(job_db, tmp_path, monkeypatch, "code", node, job)
    job_db.update_workspace(
        WS, node_config={WS: {NODE["code"]: {"timeout_seconds": {"nested": 1}}}, "other": {"x": {}}}
    )
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = fetch_candidates(conn, 64, 64, "code")
    assert rows
    for row in rows:
        assert not any("node_config" in column for column in row)
        assert not isinstance(row[WORKSPACE_TIMEOUT_COLUMN], (dict, list))
    claimed = _broker(tmp_path).claim("worker-1")
    assert claimed is not None
    assert claimed.manifest["config_resolution"]["timeout_seconds"] == {
        "value": 600,
        "source": "workspace_override_invalid",
    }
