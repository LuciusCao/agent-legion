"""Host-local code lease and authority evidence for default code=0 measurement."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from scripts.stress.worker_resource_execution import (
    HOST_CODE_EXECUTOR_ID,
    ContractViolationError,
)
from scripts.stress.worker_resource_execution import (
    require_exclusive_execution_routes as require_exclusive_execution_routes,
)


def partition_execution_expectations(
    expected: Mapping[tuple[str, str, int], Mapping[str, Any]], allow_host_code: bool
) -> tuple[
    dict[tuple[str, str, int], Mapping[str, Any]], dict[tuple[str, str, int], Mapping[str, Any]]
]:
    host = {
        key: value for key, value in expected.items() if allow_host_code and value["kind"] == "code"
    }
    return host, {key: value for key, value in expected.items() if key not in host}


def read_host_code_leases(conn: Any, job_ids: list[str], enabled: bool) -> list[Mapping[str, Any]]:
    if not enabled:
        return []
    return conn.execute(
        "select execution_id, executor_id, workspace_id, job_id, node_key, node_run_id, status"
        " from executor_leases where job_id = any(%s) and executor_id = %s",
        (job_ids, HOST_CODE_EXECUTOR_ID),
    ).fetchall()


def require_host_code_leases(
    expected: Mapping[tuple[str, str, int], Mapping[str, Any]],
    lease_rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str, int], Mapping[str, Any]]:
    rows = [row for row in lease_rows if row.get("executor_id") == HOST_CODE_EXECUTOR_ID]
    done: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    execution_ids: set[str] = set()
    for key, expectation in expected.items():
        job_id, node_key, node_run_id = key
        candidates = [
            row
            for row in rows
            if str(row["job_id"]) == job_id
            and str(row["node_key"]) == node_key
            and int(row["node_run_id"]) == node_run_id
        ]
        if len(candidates) != 1:
            raise ContractViolationError(f"expected one current Host code lease for {key}")
        row = candidates[0]
        if str(row["workspace_id"]) != expectation["workspace_id"] or row["status"] != "released":
            raise ContractViolationError(
                f"Host code lease identity or terminal state mismatch for {key}"
            )
        execution_id = str(row["execution_id"])
        if not execution_id or execution_id in execution_ids:
            raise ContractViolationError(f"duplicate Host code execution id for {key}")
        execution_ids.add(execution_id)
        done[key] = row
    return done


def host_code_direct_refs(
    execution: Mapping[str, Any],
    expected_names: Iterable[str],
    workspace_id: str,
    authority: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for name in sorted(expected_names):
        row = authority.get((str(execution["job_id"]), str(execution["node_key"]), name))
        if row is None:
            raise ContractViolationError(f"Host code output {name} has no authority row")
        key = (
            f"jobs-staging/{workspace_id}/{execution['job_id']}/{execution['execution_id']}/{name}"
        )
        storage_key = str(row.get("storage_key") or "")
        if storage_key.endswith(".gz"):
            key += ".gz"
        refs[name] = {
            "storage_key": key,
            "size_bytes": row["size_bytes"],
            "content_hash": row["content_hash"],
        }
    return refs
