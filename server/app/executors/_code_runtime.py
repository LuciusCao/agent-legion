"""Runtime assembly and parent-side privileged actions for the code executor.

Split from ``code.py`` to keep it within its size budget; mirrors the
``_code_sandbox.py`` layout (functions take the executor instance and reach
its internals). Since #96 every code child is the sandboxed child.

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
from server.app.services.material_cache import prefetch_material_block
from server.app.services.run_payload import sdk_batch_row
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
        # Host root: nodes resolve machine-relative asset paths against it
        # instead of ``__file__`` (meaningless for DB-loaded code text).
        "root_dir": str(executor._repo_root),
        # Section-whitelisted (VAULT-SECRET-001): the sandboxed child is user
        # code, so vault/auth/database/agent_workers sections never cross.
        "settings_config": node_safe_settings_config(executor.settings_config),
        "node_config": dict(context.node_config),
        "cancellation": token,
    }
    if executor.job_db is not None:
        run_id = str(context.job.get("run_id") or "")
        if run_id:
            run = executor.job_db.get_run(run_id)
            # SDK-facing batch row: run columns plus the payload rebuilt from
            # the authoritative run/job freeze columns (RUN-FREEZE-001).
            batch_row = sdk_batch_row(run, context.job)
            if batch_row:
                runtime["job_batch"] = batch_row
        runtime["skill_versions"] = _prefetch_skill_versions(executor, context)
    materials = prefetch_material_block(executor, context.job, str(context.workspace_id))
    if materials is not None:
        runtime["materials"] = materials
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
        # #204 broad-except audit: deliberate degradation, per the module
        # contract above — a transient DB error must not fail the node over
        # an AUDIT input (skill versions), so the mapping degrades to empty
        # and the node runs without the version hints. list_node_runs is a
        # bare SQL read with no business exception family; exc_info keeps
        # the DB root cause visible at debug level (this is a soft input,
        # not worth an operator-facing error).
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
    # #187 step 3: the facade's `.path` is private; the DSN for the
    # privileged token invalidation comes from `dsn_identity` (the only
    # public accessor). Shapes without it (job_db-less tests, plain
    # objects) degrade to a no-op exactly like the old getattr default.
    dsn = str(getattr(executor.job_db, "dsn_identity", "") or "").strip()
    if not key or not dsn:
        return
    try:
        ConnectionTokenService(dsn).report_auth_failure(key)
    except Exception:  # reporting must never mask the node's own failure
        # #204 broad-except audit: post-node privileged action. The node's
        # result (completed/failed, artifacts) is already determined and this
        # runs after the child exits, so an invalidation failure — the delete
        # above is a bare SQL write — must not convert a finished node into
        # a thrown error and lose the result. The consequence is bounded and
        # self-healing: the stale cached token is rejected again on the next
        # upstream auth failure and re-reported; logger.exception keeps the
        # traceback. Caller is the executor's output path, not a retry loop.
        logger.exception("connection %s: failed to report auth failure", key)
