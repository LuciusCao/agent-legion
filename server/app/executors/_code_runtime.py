"""Runtime assembly and parent-side privileged actions for the code executor.

Split from ``code.py`` to keep it within its size budget; mirrors the
``_code_sandbox.py`` layout (functions take the executor instance and reach
its internals).

Design: ``docs/architecture/node-sdk-and-worker-execution-design.md`` §3/§5.
Builtin and sandboxed children share one runtime contract: every DB-derived
input is prefetched here, in the parent, so node code never holds a database
handle. Privileged actions (connection-token invalidation) likewise stay in
the parent — nodes only record facts (the auth-failure marker).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.config_schema import node_safe_settings_config
from server.app.executors.cancellation import CancellationToken
from server.app.executors.models import ExecutionContext
from server.app.services.connection_tokens import ConnectionTokenService
from workspace_libs.node_sdk import AUTH_FAILURE_MARKER_PATH

if TYPE_CHECKING:
    from server.app.executors.code import CodeExecutor

logger = logging.getLogger(__name__)


def build_runtime(
    executor: CodeExecutor, context: ExecutionContext, token: CancellationToken
) -> dict[str, Any]:
    """Assemble the runtime dict both child flavors (builtin/sandboxed) share."""
    runtime: dict[str, Any] = {
        "job_dir": context.job_dir,
        "log_path": context.log_path,
        "inputs": context.inputs,
        "expected_outputs": context.expected_outputs,
        "capability": context.capability,
        "node_key": context.node_key,
        "workflow_key": context.workflow_key,
        "execution_id": context.execution_id,
        "workspace_id": context.workspace_id,
        "workspace": dict(context.workspace),
        "job": dict(context.job),
        # Section-whitelisted (VAULT-SECRET-001): the sandboxed child is user
        # code, so vault/auth/database/agent_workers sections never cross.
        "settings_config": node_safe_settings_config(executor.settings_config),
        "node_config": dict(context.node_config),
        "cancellation": token,
    }
    if executor.job_db is not None:
        batch_id = str(context.job.get("batch_id") or "")
        if batch_id:
            batch = executor.job_db.get_batch(batch_id)
            if batch:
                runtime["job_batch"] = dict(batch)
        runtime["skill_versions"] = _prefetch_skill_versions(executor, context)
    return runtime


def _prefetch_skill_versions(executor: CodeExecutor, context: ExecutionContext) -> dict[str, str]:
    """Collect ``node_key -> skill_version`` from this job's node runs.

    Best-effort like the retired node-side ``collect_skill_versions``: a
    transient DB error degrades to an empty mapping instead of failing
    the node.
    """
    job_id = str(context.job.get("id") or "")
    job_db = executor.job_db
    if not job_id or job_db is None:
        return {}
    try:
        runs = job_db.list_node_runs(job_id)
    except Exception:
        logger.debug("list_node_runs failed for job %s", job_id, exc_info=True)
        return {}
    return {
        str(run["node_key"]): str(run["skill_version"])
        for run in runs
        if run.get("node_key") and run.get("skill_version")
    }


def clear_auth_failure_marker(context: ExecutionContext) -> None:
    """Drop any stale marker: a previous attempt must not fake a fresh report."""
    with contextlib.suppress(OSError):
        (context.job_dir / AUTH_FAILURE_MARKER_PATH).unlink(missing_ok=True)


def consume_auth_failure_marker(executor: CodeExecutor, context: ExecutionContext) -> None:
    """Perform the privileged token invalidation a node asked for.

    ``NodeContext.report_auth_failure`` only drops a marker under ``job_dir``;
    the parent re-reads the connection key, invalidates the cached connection
    token, and removes the marker. Runs after the child exits — the cached
    token is only consulted at the next dispatch, so the semantics match the
    retired in-child report.
    """
    marker = context.job_dir / AUTH_FAILURE_MARKER_PATH
    try:
        if not marker.is_file():
            return
        key = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return
    finally:
        with contextlib.suppress(OSError):
            marker.unlink(missing_ok=True)
    if not key and isinstance(context.node_config, Mapping):
        key = str(context.node_config.get("connection") or "").strip()
    dsn = str(getattr(executor.job_db, "path", "") or "").strip()
    if not key or not dsn:
        return
    try:
        ConnectionTokenService(dsn).report_auth_failure(key)
    except Exception:  # reporting must never mask the node's own failure
        logger.exception("connection %s: failed to report auth failure", key)
