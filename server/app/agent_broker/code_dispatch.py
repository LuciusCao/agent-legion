"""Code-payload dispatch: build kind='code' manifests/bundles and enqueue.

Batch 2 (design §7): a worker-eligible code node ships to a remote Worker as
a self-contained payload — the code text plus a ``workspace_libs`` snapshot
ride the bundle; the queued manifest carries only non-secret config plus
vault ``secret_ref`` markers, and the claim response re-resolves secrets on
the fly (VAULT-SECRET-001, in code_manifest_config). The persisted ``runtime_context`` is a
lightweight audit stub (issue #142); the claim response rebuilds the full
DB-derived payloads in memory (``code_manifest.resolve_code_runtime_context``).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import tarfile
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from server.app.agent_broker.agent_artifacts import stage_agent_inputs
from server.app.agent_broker.agent_bundle import (
    CODE_BUNDLE_LIBS_DIR,
    CODE_BUNDLE_NODE_FILE,
    cleanup_bundle_on_error,
)
from server.app.agent_broker.broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.claim_paths import claim_log_path
from server.app.agent_broker.code_manifest import runtime_context_stub
from server.app.agent_broker.dispatch_pool import AgentEnqueuePool
from server.app.agent_control.registry import CODE_PROTOCOL_VERSION as _CODE_PROTOCOL_VERSION
from server.app.agent_control.registry import ONLINE_THRESHOLD_SECONDS as _ONLINE_THRESHOLD_SECONDS
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection
from server.app.executors.contracts import CodeCapabilityConfig
from server.app.executors.models import ExecutionContext
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactStore
from server.app.services.run_payload import sdk_batch_row
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowNode

logger = logging.getLogger(__name__)

# Online-probe TTL: the workflow worker polls several times a second, and the
# probe answer may lag a Worker (dis)appearing by a few seconds without harm
# (a stale "online" just queues a claimable request).
_ONLINE_PROBE_TTL_SECONDS = 5.0


def _json_safe(value: Any) -> Any:
    """JSON round-trip so DB row dicts (datetimes) fit the manifest document."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def build_code_bundle(bundle_path: Path, *, code_text: str, workspace_libs_dir: Path) -> None:
    """Tar the node code text plus a ``workspace_libs`` snapshot (batch 2).

    No ``manifest.json`` here on purpose: the code manifest is delivered in
    the claim response, where the Host injects resolved secrets — embedding a
    copy in the bundle would create a second, secret-free-but-stale source of
    truth (VAULT-SECRET-001).
    """
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path, "w:gz") as tar:
        tar.add(
            workspace_libs_dir,
            arcname=CODE_BUNDLE_LIBS_DIR,
            filter=lambda member: None if "__pycache__" in member.name else member,
        )
        data = code_text.encode("utf-8")
        info = tarfile.TarInfo(CODE_BUNDLE_NODE_FILE)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def has_online_code_worker(database_dsn: ConnectSource, capability: str, workspace_id: str) -> bool:
    """True when an online Worker can claim code executions in *workspace_id*.

    Mirrors the claim-side filters (protocol v2, code capacity, workspace
    admission) — an inadmissible Worker would wedge the job in queued for
    good. ``capability`` no longer filters anything (issue #284); it stays
    for caller/cache compatibility."""
    del capability  # admission no longer matches capabilities (issue #284)
    with read_connection(database_dsn) as conn:
        row = conn.execute(
            "select 1 from agent_workers where revoked_at is null"
            " and max_code_concurrency > 0 and protocol_version >= %s"
            " and last_seen_at > now() - make_interval(secs => %s)"
            " and (allowed_workspaces_json::jsonb = '[]'::jsonb or allowed_workspaces_json::jsonb @> jsonb_build_array(%s::text))"
            " limit 1",
            (_CODE_PROTOCOL_VERSION, _ONLINE_THRESHOLD_SECONDS, workspace_id),
        ).fetchone()
    return row is not None


def has_online_code_workers(database_dsn: ConnectSource) -> bool:
    """Any online code Worker exists (#389 pass gate); workspace admission is
    the claim transaction's business. Predicate: ``AgentWorkerRegistry``."""
    return AgentWorkerRegistry(database_dsn).count_online_code_workers() > 0


class CodeDispatchService:
    """Build immutable code payloads and enqueue them without starting a run."""

    def __init__(
        self,
        settings: Settings,
        broker: AgentExecutionBroker,
        artifact_store: ArtifactStore,
        job_db: JobQueries,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.artifact_store = artifact_store
        self.job_db = job_db
        enqueue_config = settings.executor_runtime.agent_enqueue
        self.enqueue_pool = AgentEnqueuePool(
            workers=enqueue_config.workers, max_pending=enqueue_config.max_pending
        )
        self._in_flight: set[tuple[str, str]] = set()
        self._in_flight_lock = threading.Lock()
        self._online_probe: dict[tuple[str, str], tuple[float, bool]] = {}

    def online_code_worker_available(self, capability: str, workspace_id: str) -> bool:
        """TTL-cached probe (per capability+workspace): an online Worker can claim it?"""
        now = time.monotonic()
        probed_at, result = self._online_probe.get((capability, workspace_id), (0.0, False))
        if now - probed_at >= _ONLINE_PROBE_TTL_SECONDS:
            result = has_online_code_worker(self.job_db, capability, workspace_id)
            self._online_probe[(capability, workspace_id)] = (now, result)
        return result

    def is_in_flight(self, job_id: str, node_key: str) -> bool:
        with self._in_flight_lock:
            return (job_id, node_key) in self._in_flight

    def try_mark_in_flight(self, job_id: str, node_key: str) -> bool:
        with self._in_flight_lock:
            key = (job_id, node_key)
            if key in self._in_flight:
                return False
            self._in_flight.add(key)
            return True

    def discard_in_flight(self, job_id: str, node_key: str) -> None:
        with self._in_flight_lock:
            self._in_flight.discard((job_id, node_key))

    def enqueue(
        self,
        *,
        capability: str,
        capability_config: CodeCapabilityConfig,
        workspace: dict[str, Any],
        job: dict[str, Any],
        workflow_key: str,
        node: WorkflowNode,
        job_dir: Path,
        log_path: Path,
        inputs: tuple[str, ...],
        code_text: str,
        custom_code: bool,
        config: dict[str, Any],
        secret_config: dict[str, Any],
        shard_runtime: dict[str, Any] | None = None,
    ) -> bool:
        """Stage inputs, build the bundle, and enqueue a kind='code' request.

        ``shard_runtime`` (#389): a shard execution's ``shard_index`` /
        ``shard_input`` payload. It is written into the PERSISTED manifest
        (top-level keys) — the remote Worker reads them from there to rebuild
        the same runtime dict the local executor hands to node code, and the
        claim transaction reads ``shard_index`` to bind the execution to its
        ``node_shards`` row (dedup + shard-aware completion). #401: the
        active-request gate below matches the broker index's identity —
        per-shard for shard rows, plain node-level otherwise."""
        if shard_runtime is not None:
            if self.broker.has_active_request(
                str(job["id"]), node.key, shard_index=int(shard_runtime["shard_index"])
            ):
                return False
        elif self.broker.has_active_request(str(job["id"]), node.key):
            return False
        execution_id = str(uuid.uuid4())
        digest = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
        manifest: dict[str, Any] = {
            "kind": "code",
            "execution_id": execution_id,
            "workspace_id": str(workspace["id"]),
            "job_id": str(job["id"]),
            "workflow_key": workflow_key,
            "node_key": node.key,
            "capability": capability,
            "code_hash": digest,
            "custom_code": custom_code,
            # Frozen so claim-time injection validates against the same
            # schema the config was resolved with.
            "config_schema": capability_config.config_schema,
            "config": config,
            "secret_config": secret_config,
            "inputs": list(inputs),
            "expected_outputs": list(node.outputs),
            "timeout_seconds": capability_config.timeout_seconds,
            "sandbox_network": capability_config.sandbox_network,
            "log_path": claim_log_path({"log_path": str(log_path)}, self.settings.data_dir),
            # Runtime context the Worker uses to rebuild the same runtime
            # dict the local executor hands to node code (design §3). Issue
            # #142: only lightweight audit references persist (batch_id +
            # hash) — embedding the full job_batch payload cost ~1.7MB per
            # row and grew agent_execution_requests to ~198G of TOAST. The
            # claim-response path rebuilds the payloads in memory
            # (resolve_code_runtime_context); nothing heavy is persisted.
            "runtime_context": runtime_context_stub(job, workspace, self._prefetch_job_batch(job)),
            # Shard identity rides the manifest top level (#389): the claim
            # transaction flips the node_shards row (dedup by execution_id),
            # the Worker injects the payload into the child runtime dict.
            **(shard_runtime or {}),
        }
        if shard_runtime is not None:
            # The shard's output rides the ARCHIVE as a regular expected
            # output (same filename contract as the local executor,
            # _shard_contract.shard_output_name): no size-capped metadata
            # channel; completion reads it from the unpacked job_dir.
            manifest["expected_outputs"] = [
                *manifest["expected_outputs"],
                f"shard_output-{shard_runtime['shard_index']}.json",
            ]
        context = ExecutionContext(
            execution_id=execution_id,
            lease_id="",
            node_run_id=0,
            executor_id=f"agent:code:{capability}",
            workspace_id=str(workspace["id"]),
            job_id=str(job["id"]),
            workflow_key=workflow_key,
            node_key=node.key,
            capability=capability,
            workspace=workspace,
            job=job,
            job_dir=job_dir,
            log_path=log_path,
            inputs=inputs,
            expected_outputs=tuple(node.outputs),
            runtime={"node_execution": asdict(node.execution), **(shard_runtime or {})},
        )
        stage_agent_inputs(self.artifact_store, context, manifest)
        if self.broker.bundle_dir is None:
            raise RuntimeError("Agent bundle directory is not configured")
        bundle_path = self.broker.bundle_dir / f"{execution_id}.tar.gz"
        with cleanup_bundle_on_error(bundle_path):
            build_code_bundle(
                bundle_path,
                code_text=code_text,
                workspace_libs_dir=Path(self.settings.root_dir) / "workspace_libs",
            )
            manifest["bundle_name"] = bundle_path.name
            queued = self.broker.enqueue(
                AgentExecutionRequest(
                    workspace_id=str(workspace["id"]),
                    job_id=str(job["id"]),
                    workflow_key=workflow_key,
                    node_key=node.key,
                    # For kind='code' rows agent_id carries the capability and
                    # the hash carries the code hash (no Agent definition).
                    agent_id=capability,
                    agent_definition_hash=digest,
                    manifest=manifest,
                    execution_id=execution_id,
                    kind="code",
                )
            )
            if queued is None:
                bundle_path.unlink(missing_ok=True)
            return queued is not None

    def _prefetch_job_batch(self, job: dict[str, Any]) -> Any:
        """The SDK-facing run row (node SDK ``ctx.batch``), JSON-safe or None.

        Only the audit hash of the row is persisted (issue #142); the full
        payload is re-fetched on the claim-response path instead.
        """
        run_id = str(job.get("run_id") or "")
        if not run_id:
            return None
        try:
            run = self.job_db.get_run(run_id)
        except Exception:
            # #204 broad-except audit: deliberate degradation on the enqueue
            # path. The batch row is an SDK convenience (ctx.batch); only its
            # audit hash was persisted (#142) and the claim-response path
            # re-fetches it strictly, so a transient DB error here degrades
            # to None (debug log) instead of failing the dispatch — the poll
            # pass would only retry the same enqueue next time anyway. The
            # DB driver surface is not a business-exception family.
            logger.debug("get_run failed for job %s", job.get("id"), exc_info=True)
            return None
        batch_row = sdk_batch_row(run, job)
        return _json_safe(batch_row) if batch_row else None
