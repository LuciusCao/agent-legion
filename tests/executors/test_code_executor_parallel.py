"""Parallel code-node result attribution (#1311).

0.8.0 UI acceptance finding: a start fan-out ran two code nodes of the SAME
job in parallel on the local code pool — ``afail`` (raises) and ``f01``
(writes its output). ``f01`` actually succeeded (complete log, artifact
written) but node_runs/job_nodes recorded it failed, exit_code=1, with
``afail``'s error_message verbatim. Both executions share the job's single
``job_dir``; the result file the sandboxed child writes and the parent reads
lived at one fixed per-job name, so the failing neighbour's payload could be
read as this execution's outcome. The file is now namespaced per execution.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from server.app.executors.code import CodeExecutor
from server.app.executors.models import ExecutionContext, ExecutionResult
from tests.helpers.velites_sandbox import sandboxed as _sandboxed

REPO_ROOT = Path(__file__).resolve().parents[2]


class _SlowEvent:
    """Never-set event with a fixed 0.5s wait: stretches the parent's
    exit-detection granularity so the sibling's overwrite deterministically
    lands before this execution's result read (the #1311 race window)."""

    def is_set(self) -> bool:
        return False

    def wait(self, timeout: float | None = None) -> bool:
        time.sleep(0.5)
        return False

    def set(self) -> None:
        pass


def _context(job_dir: Path, node_key: str, expected_outputs: tuple[str, ...]) -> ExecutionContext:
    from server.app.executors.cancellation import CancellationToken

    return ExecutionContext(
        execution_id=f"exec-{node_key}",
        lease_id=f"lease-{node_key}",
        node_run_id=1,
        executor_id="code",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="wf",
        node_key=node_key,
        capability=node_key,
        workspace={"id": "ws-a"},
        job={
            "id": "job-1",
            "workspace_id": "ws-a",
            "workflow_key": "wf",
            "run_id": "",
            "storage_dir": str(job_dir),
        },
        job_dir=job_dir,
        log_path=job_dir / f"{node_key}.log",
        inputs=(),
        expected_outputs=expected_outputs,
        runtime={"cancellation": CancellationToken(_SlowEvent())},
    )


def test_parallel_nodes_results_attributed_to_own_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1311 recipe: afail (raise) + f01 (ok) run concurrently in one job_dir.

    afail's node code waits until the sibling's result file appears before
    raising, so pre-fix its error payload overwrites the file f01's parent is
    about to read; the slow poll token keeps f01's parent from reading in the
    gap between its own child's exit and the neighbour's overwrite.
    """
    _sandboxed(monkeypatch)
    executor = CodeExecutor(repo_root=REPO_ROOT)
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    f01_code = """
def run(job, job_dir, runtime):
    (job_dir / "f01.json").write_text("{}", encoding="utf-8")
"""
    afail_code = """
import time


def run(job, job_dir, runtime):
    deadline = time.monotonic() + 60
    while not list(job_dir.glob(".custom_node_result*")):
        if time.monotonic() > deadline:
            raise RuntimeError("sibling result never appeared")
        time.sleep(0.01)
    raise RuntimeError("afail-boom")
"""

    results: dict[str, ExecutionResult] = {}
    errors: dict[str, BaseException] = {}

    def run_node(node_key: str, code: str, outputs: tuple[str, ...]) -> None:
        try:
            results[node_key] = executor.execute(
                replace(_context(job_dir, node_key, outputs), node_code=code)
            )
        except BaseException as exc:  # surfaced below with the node key
            errors[node_key] = exc

    threads = [
        threading.Thread(target=run_node, args=("f01", f01_code, ("f01.json",))),
        threading.Thread(target=run_node, args=("afail", afail_code, ())),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), "a node execution hung"
    assert not errors, errors

    assert results["afail"].status == "failed"
    assert "afail-boom" in results["afail"].error_message
    # The #1311 pin: the healthy neighbour keeps its own outcome — pre-fix it
    # was recorded failed with afail's error_message verbatim.
    assert results["f01"].status == "completed", (
        f"f01 misattributed: {results['f01'].status} {results['f01'].error_message!r}"
    )
    assert results["f01"].error_message == ""
    assert (job_dir / "f01.json").is_file()
