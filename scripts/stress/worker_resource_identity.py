"""Exact execution identity, direct-output and authority evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from server.app.db.transaction import read_connection
from server.app.services.job_artifact_gzip import content_stream
from server.app.services.job_artifact_objects import artifact_storage_key
from server.app.storage import ObjectStorage

_HASH = re.compile(r"^[0-9a-f]{64}$")


class ContractViolationError(RuntimeError):
    """A visible result is invalid; readiness retries must stop immediately."""


class ExecutionReadinessError(RuntimeError):
    """The normal two-phase commit has not published the expected execution yet."""


def build_execution_expectations(
    *,
    job_ids: Iterable[str],
    workspace_id: str,
    nodes: Mapping[str, Mapping[str, Any]],
    job_rows: Iterable[Mapping[str, Any]],
    job_node_rows: Iterable[Mapping[str, Any]],
    node_run_rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str, int], dict[str, Any]]:
    """Build exact current node identities, independent of execution-row counts."""
    job_id_list = list(job_ids)
    expected_job_ids = set(job_id_list)
    if not expected_job_ids or len(job_id_list) != len(expected_job_ids):
        raise ContractViolationError("measurement job ids are empty or duplicated")
    jobs = {str(row["id"]): row for row in job_rows}
    if len(jobs) != len(expected_job_ids) or set(jobs) != expected_job_ids:
        raise ContractViolationError("measurement job identity set is incomplete or duplicated")
    if any(str(row["workspace_id"]) != workspace_id for row in jobs.values()):
        raise ContractViolationError("measurement job belongs to another workspace")
    actual_nodes: dict[str, set[str]] = {job_id: set() for job_id in expected_job_ids}
    for row in job_node_rows:
        actual_nodes[str(row["job_id"])].add(str(row["node_key"]))
    current_runs: dict[tuple[str, str], int] = {}
    for row in node_run_rows:
        key = (str(row["job_id"]), str(row["node_key"]))
        current_runs[key] = max(current_runs.get(key, -1), int(row["id"]))
    expected: dict[tuple[str, str, int], dict[str, Any]] = {}
    for job_id in sorted(expected_job_ids):
        if actual_nodes[job_id] != set(nodes):
            raise ContractViolationError(f"job {job_id} node set does not match its DAG")
        for node_key, node in nodes.items():
            run_id = current_runs.get((job_id, node_key))
            if run_id is None:
                raise ExecutionReadinessError(
                    f"job {job_id} node {node_key} has no current node_run"
                )
            expected[(job_id, node_key, run_id)] = {
                "workspace_id": workspace_id,
                "kind": node["type"],
                "outputs": list(node["outputs"]),
            }
    return expected


def require_done_executions(
    expected: Mapping[tuple[str, str, int], Mapping[str, Any]],
    execution_rows: Iterable[Mapping[str, Any]],
    *,
    worker_id: str,
) -> dict[tuple[str, str, int], Mapping[str, Any]]:
    """Match current node_run identities one-to-one; history cannot fill a missing job."""
    rows = list(execution_rows)
    done: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    execution_ids: set[str] = set()
    for key, expectation in expected.items():
        job_id, node_key, node_run_id = key
        candidates = [
            row
            for row in rows
            if str(row["job_id"]) == job_id
            and str(row["node_key"]) == node_key
            and row.get("node_run_id") is not None
            and int(row["node_run_id"]) == node_run_id
        ]
        if not candidates:
            raise ExecutionReadinessError(f"execution for {key} is not visible")
        if len(candidates) != 1:
            raise ContractViolationError(f"duplicate current execution identities for {key}")
        row = candidates[0]
        if (
            str(row["workspace_id"]) != expectation["workspace_id"]
            or str(row["kind"]) != expectation["kind"]
            or str(row["worker_id"]) != worker_id
        ):
            raise ContractViolationError(f"execution identity mismatch for {key}")
        if row["state"] != "done" or not row.get("outcome_json"):
            raise ExecutionReadinessError(f"execution outcome for {key} is not visible")
        execution_id = str(row["execution_id"])
        if not execution_id or execution_id in execution_ids:
            raise ContractViolationError(f"duplicate execution id for {key}")
        execution_ids.add(execution_id)
        done[key] = row
    return done


def require_direct_outputs(
    execution: Mapping[str, Any], expected_names: Iterable[str], workspace_id: str
) -> dict[str, dict[str, Any]]:
    """Require every declared output to carry its complete direct staging ref."""
    outcome_raw = execution.get("outcome_json")
    outcome = json.loads(outcome_raw) if isinstance(outcome_raw, str) else outcome_raw
    if not isinstance(outcome, Mapping):
        raise ContractViolationError(
            f"execution {execution.get('execution_id')} has no final outcome"
        )
    outputs = outcome.get("output_artifacts")
    expected = set(expected_names)
    if not expected or not isinstance(outputs, Mapping) or set(outputs) != expected:
        raise ContractViolationError(
            f"execution {execution.get('execution_id')} output set mismatch: "
            f"expected {sorted(expected)}, got {sorted(outputs) if isinstance(outputs, Mapping) else type(outputs).__name__}"
        )
    refs: dict[str, dict[str, Any]] = {}
    for name in sorted(expected):
        ref = outputs[name]
        if not isinstance(ref, Mapping):
            raise ContractViolationError(
                f"execution {execution['execution_id']} output {name} used legacy CAS or missing ref"
            )
        storage_key = ref.get("storage_key")
        size = ref.get("size_bytes")
        digest = ref.get("content_hash")
        expected_key = (
            f"jobs-staging/{workspace_id}/{execution['job_id']}/{execution['execution_id']}/{name}"
        )
        if not isinstance(storage_key, str) or storage_key not in {
            expected_key,
            expected_key + ".gz",
        }:
            raise ContractViolationError(
                f"execution {execution['execution_id']} output {name} has wrong staging key"
            )
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ContractViolationError(
                f"execution {execution['execution_id']} output {name} has invalid size"
            )
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ContractViolationError(
                f"execution {execution['execution_id']} output {name} has invalid hash"
            )
        refs[name] = {
            "storage_key": storage_key,
            "size_bytes": size,
            "content_hash": digest,
        }
    return refs


def require_authority_key(
    workspace_id: str, job_id: str, name: str, key: Any, staging_key: str
) -> str:
    if not isinstance(key, str) or key.endswith(".gz") != staging_key.endswith(".gz"):
        raise ContractViolationError("staging/authority gzip form mismatch")
    legacy = artifact_storage_key(workspace_id, job_id, name)
    if key in {legacy, legacy + ".gz"}:
        return key
    prefix = f"jobs/{workspace_id}/{job_id}/.v/"
    if key.startswith(prefix):
        version, separator, artifact_name = key[len(prefix) :].partition("/")
        if separator and version not in {"", ".", ".."} and artifact_name in {name, name + ".gz"}:
            return key
    raise ContractViolationError(f"authority key is not for {workspace_id}/{job_id}/{name}")


def verify_authority_object(
    storage: ObjectStorage,
    *,
    execution_id: str,
    workspace_id: str,
    job_id: str,
    name: str,
    ref: Mapping[str, Any],
    row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Cross-check direct ref, promoted manifest row, HEAD size and content digest."""
    if row is None:
        raise ContractViolationError(
            f"execution {execution_id} output {name} has no job_artifacts row"
        )
    authority_key = require_authority_key(
        workspace_id, job_id, name, row.get("storage_key"), str(ref["storage_key"])
    )
    if int(row.get("size_bytes", -1)) != int(ref["size_bytes"]):
        raise ContractViolationError(
            f"execution {execution_id} output {name} manifest size mismatch"
        )
    if row.get("content_hash") != ref["content_hash"]:
        raise ContractViolationError(
            f"execution {execution_id} output {name} manifest hash mismatch"
        )
    head = storage.head_object(authority_key)
    if head is None:
        raise ContractViolationError(
            f"execution {execution_id} output {name} authority object is missing"
        )
    if head.size_bytes != int(ref["size_bytes"]):
        raise ContractViolationError(f"execution {execution_id} output {name} object size mismatch")
    digest = hashlib.sha256()
    with content_stream(storage, authority_key) as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != ref["content_hash"]:
        raise ContractViolationError(f"execution {execution_id} output {name} object hash mismatch")
    return {
        "execution_id": execution_id,
        "output": name,
        "direct_ref": dict(ref),
        "authority_key": authority_key,
        "object_verified": True,
    }


def verify_remote_uploads(
    *,
    dsn: str,
    storage: ObjectStorage,
    workspace_id: str,
    nodes: Mapping[str, Mapping[str, Any]],
    job_ids: list[str],
    worker_id: str,
) -> dict[str, Any]:
    """Wait-ready exact executions, then validate direct refs and authority bytes."""
    if not job_ids:
        raise ContractViolationError("measurement wave has no jobs")
    with read_connection(dsn) as conn:
        job_rows = conn.execute(
            "select id, workspace_id from jobs where id = any(%s)", (job_ids,)
        ).fetchall()
        job_node_rows = conn.execute(
            "select job_id, node_key from job_nodes where job_id = any(%s)", (job_ids,)
        ).fetchall()
        node_run_rows = conn.execute(
            "select id, job_id, node_key from node_runs where job_id = any(%s)",
            (job_ids,),
        ).fetchall()
        execution_rows = conn.execute(
            "select execution_id, workspace_id, job_id, node_key, node_run_id,"
            " kind, worker_id, state, outcome_json from agent_execution_requests"
            " where job_id = any(%s)",
            (job_ids,),
        ).fetchall()
    expected = build_execution_expectations(
        job_ids=job_ids,
        workspace_id=workspace_id,
        nodes=nodes,
        job_rows=job_rows,
        job_node_rows=job_node_rows,
        node_run_rows=node_run_rows,
    )
    done = require_done_executions(expected, execution_rows, worker_id=worker_id)
    with read_connection(dsn) as conn:
        artifact_rows = conn.execute(
            "select job_id, node_key, name, storage_key, size_bytes, content_hash"
            " from job_artifacts where job_id = any(%s)",
            (job_ids,),
        ).fetchall()
    authority = {(row["job_id"], row["node_key"], row["name"]): row for row in artifact_rows}
    artifacts: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    for key, execution in done.items():
        job_id, node_key, node_run_id = key
        expectation = expected[key]
        refs = require_direct_outputs(execution, expectation["outputs"], workspace_id)
        identities.append(
            {
                "workspace_id": workspace_id,
                "job_id": job_id,
                "node_key": node_key,
                "node_run_id": node_run_id,
                "kind": execution["kind"],
                "execution_id": execution["execution_id"],
            }
        )
        for name, ref in refs.items():
            artifacts.append(
                verify_authority_object(
                    storage,
                    execution_id=execution["execution_id"],
                    workspace_id=workspace_id,
                    job_id=job_id,
                    name=name,
                    ref=ref,
                    row=authority.get((job_id, node_key, name)),
                )
            )
    return {
        "remote_execution_verified": True,
        "direct_upload_verified": True,
        "execution_identities": identities,
        "artifacts": artifacts,
    }
