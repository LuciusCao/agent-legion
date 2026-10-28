"""Exact current-execution identity matching for Worker measurement."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


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


HOST_CODE_EXECUTOR_ID = "code"


def _matches_current_key(row: Mapping[str, Any], key: tuple[str, str, int]) -> bool:
    job_id, node_key, node_run_id = key
    return (
        str(row["job_id"]) == job_id
        and str(row["node_key"]) == node_key
        and row.get("node_run_id") is not None
        and int(row["node_run_id"]) == node_run_id
    )


def require_exclusive_execution_routes(
    remote_expected: Mapping[tuple[str, str, int], Mapping[str, Any]],
    host_expected: Mapping[tuple[str, str, int], Mapping[str, Any]],
    execution_rows: Iterable[Mapping[str, Any]],
    lease_rows: Iterable[Mapping[str, Any]],
    remote_done: Mapping[tuple[str, str, int], Mapping[str, Any]],
    host_done: Mapping[tuple[str, str, int], Mapping[str, Any]],
) -> None:
    executions = list(execution_rows)
    leases = [row for row in lease_rows if row.get("executor_id") == HOST_CODE_EXECUTOR_ID]
    for key in host_expected:
        if any(_matches_current_key(row, key) for row in executions):
            raise ContractViolationError(f"conflicting Worker execution for Host key {key}")
    for key in remote_expected:
        if any(_matches_current_key(row, key) for row in leases):
            raise ContractViolationError(f"conflicting Host lease for Worker key {key}")
    remote_ids = {str(row["execution_id"]) for row in remote_done.values()}
    host_ids = {str(row["execution_id"]) for row in host_done.values()}
    if remote_ids & host_ids:
        raise ContractViolationError("Worker and Host execution ids are not disjoint")


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
        candidates = [row for row in rows if _matches_current_key(row, key)]
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
