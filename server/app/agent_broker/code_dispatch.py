"""Code-payload dispatch: build kind='code' manifests/bundles and enqueue.

Batch 2 (design §7): a worker-eligible code-executor node is shipped to a
remote Worker as a self-contained payload — the code text (builtin repo file
or frozen custom version, resolved exactly like ``resolve_dispatch_node_code``)
plus a ``workspace_libs`` snapshot ride the bundle; the queued manifest carries
only non-secret config plus vault ``secret_ref`` markers, and the claim
response re-resolves secrets on the fly (VAULT-SECRET-001: secrets never hit
the DB, the bundle, or logs).
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
from server.app.agent_broker.dispatch_pool import AgentEnqueuePool
from server.app.agent_workers import ONLINE_THRESHOLD_SECONDS as _ONLINE_THRESHOLD_SECONDS
from server.app.config_schema import node_safe_settings_config
from server.app.db.transaction import read_connection
from server.app.executors.config import CodeCapabilityConfig
from server.app.executors.models import ExecutionContext
from server.app.jobs import JobQueries
from server.app.services.artifact_store import ArtifactStore
from server.app.services.connection_tokens import (
    ConnectionTokenService,
    inject_connection_config,
)
from server.app.services.vault import VaultService
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowNode

logger = logging.getLogger(__name__)

# Online-probe TTL: the workflow worker polls several times a second, and the
# probe answer may lag a Worker (dis)appearing by a few seconds without harm
# (a stale "online" just queues a claimable request).
_ONLINE_PROBE_TTL_SECONDS = 5.0


class PlaintextSecretError(ValueError):
    """A secret-marked config key holds a legacy plaintext value.

    Plaintext secrets can never be persisted in the queued manifest
    (VAULT-SECRET-001), so the node is not Worker-routable; the caller falls
    back to local execution, which resolves secrets in memory only.
    """


def split_manifest_config(
    schema: dict[str, Any], unresolved_config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split an UNRESOLVED node config into persistable parts.

    Returns ``(config, secret_config)``: non-secret schema-whitelisted keys
    (CONFIG-MANIFEST-001) go to ``config`` verbatim; secret-marked keys go to
    ``secret_config`` only in vault ``{"secret_ref": name}`` form (a
    reference, not a secret). A legacy plaintext secret value raises
    ``PlaintextSecretError``.
    """
    raw_properties = schema.get("properties") if isinstance(schema, dict) else None
    properties = raw_properties if isinstance(raw_properties, dict) else {}
    config: dict[str, Any] = {}
    secret_config: dict[str, Any] = {}
    for key, value in unresolved_config.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            continue
        if not prop.get("secret", False):
            config[key] = value
            continue
        if value in (None, ""):
            continue
        if isinstance(value, dict) and "secret_ref" in value:
            secret_config[key] = value
            continue
        raise PlaintextSecretError(
            f"secret config key {key!r} holds a legacy plaintext value; "
            "the node stays on local execution"
        )
    return config, secret_config


def resolve_code_manifest_config(
    manifest: dict[str, Any],
    database_dsn: Any,
    settings_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Claim-time secret injection for a claimed kind='code' manifest.

    Returns a manifest copy whose ``config`` is fully resolved (vault
    plaintext + the injected connection block), with ``secret_config``
    removed. Runs on the claim-response path only, after the claim
    transaction committed: the resolved plaintext crosses the existing HTTPS
    channel to the Worker and is never persisted.
    """
    config = {**manifest.get("config", {}), **manifest.get("secret_config", {})}
    schema = manifest.get("config_schema") or {}
    vault = VaultService(database_dsn, settings_config)
    config = vault.resolve_secret_refs(config, str(manifest.get("workspace_id") or ""))
    config = inject_connection_config(
        config, schema, ConnectionTokenService(database_dsn, settings_config)
    )
    resolved = {**manifest, "config": config}
    resolved.pop("secret_config", None)
    return resolved


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


def has_online_code_worker(database_dsn: Any, capability: str) -> bool:
    """True when an online non-revoked code Worker declares *capability*.

    Capability matching mirrors the claim-side filter (claim_evaluate.py);
    without it a mismatched Worker would let the request rot in queued —
    there is no queued-timeout fallback (batch 2 decision 3)."""
    with read_connection(database_dsn) as conn:
        row = conn.execute(
            "select 1 from agent_workers where revoked_at is null"
            " and max_code_concurrency > 0"
            " and last_seen_at > now() - make_interval(secs => %s)"
            " and (capabilities_json::jsonb @> jsonb_build_array(%s::text)"
            " or capabilities_json::jsonb @> '[\"*\"]'::jsonb)"
            " limit 1",
            (_ONLINE_THRESHOLD_SECONDS, capability),
        ).fetchone()
    return row is not None


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
        self._online_probe: dict[str, tuple[float, bool]] = {}

    def online_code_worker_available(self, capability: str) -> bool:
        """TTL-cached probe (per capability): an online Worker can claim it?"""
        now = time.monotonic()
        probed_at, result = self._online_probe.get(capability, (0.0, False))
        if now - probed_at >= _ONLINE_PROBE_TTL_SECONDS:
            result = has_online_code_worker(self.settings.database_url, capability)
            self._online_probe[capability] = (now, result)
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
    ) -> bool:
        """Stage inputs, build the bundle, and enqueue a kind='code' request."""
        if self.broker.has_active_request(str(job["id"]), node.key):
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
            # dict the local executor hands to node code (design §3); DB
            # rows cross as JSON-safe copies (datetimes become ISO strings).
            # settings_config is section-whitelisted (VAULT-SECRET-001): the
            # full settings carry the vault master key, DB DSN and register
            # token, which must never persist or leave the Host.
            "runtime_context": {
                "job": _json_safe(dict(job)),
                "workspace": _json_safe(dict(workspace)),
                "settings_config": _json_safe(node_safe_settings_config(self.settings.config)),
                "job_batch": self._prefetch_job_batch(job),
                "skill_versions": self._prefetch_skill_versions(str(job["id"])),
            },
        }
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
            runtime={"node_execution": asdict(node.execution)},
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
        """The intake batch row (node SDK ``ctx.batch``), JSON-safe or None."""
        batch_id = str(job.get("batch_id") or "")
        if not batch_id:
            return None
        try:
            batch = self.job_db.get_batch(batch_id)
        except Exception:
            logger.debug("get_batch failed for job %s", job.get("id"), exc_info=True)
            return None
        return _json_safe(dict(batch)) if batch else None

    def _prefetch_skill_versions(self, job_id: str) -> dict[str, str]:
        """Collect ``node_key -> skill_version`` from this job's node runs.

        Best-effort like the local executor's prefetch: a transient DB error
        degrades to an empty mapping instead of failing the dispatch.
        """
        if not job_id:
            return {}
        try:
            runs = self.job_db.list_node_runs(job_id)
        except Exception:
            logger.debug("list_node_runs failed for job %s", job_id, exc_info=True)
            return {}
        return {
            str(run["node_key"]): str(run["skill_version"])
            for run in runs
            if run.get("node_key") and run.get("skill_version")
        }
