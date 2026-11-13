"""Lease-scoped completion recovery and post-scan result preservation (#1350)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from worker.state_evidence import (
    EMERGENCY_EVENTS_FILENAME,
    incident_dir,
    tree_missing_inside,
)
from worker.state_evidence_sink import EMERGENCY_ATTEMPT_EVENT
from worker.upload.degraded_archive import write_agent_archive
from worker.upload.stderr_evidence import stderr_tail_for_run

if TYPE_CHECKING:
    from worker.upload.task import UploadTask


def completed_emergency_events(events: Path, lease_id: str | None = None) -> bool:
    """Whether emergency bytes prove the latest selected attempt completed.

    A lease argument requires an explicit matching attempt boundary. Within the
    selected attempt, every start resets prior evidence and only a normal final
    ``agent_end`` proves completion; any later event or malformed tail revokes it.
    ``lease_id=None`` is used only for the private staging copy after that copy
    has already passed the source lease gate.
    """
    current_attempt = lease_id is None
    started = validated = normal_end = False
    with events.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                normal_end = False
                continue
            if not isinstance(event, dict):
                normal_end = False
                continue
            kind = str(event.get("type") or "")
            if kind == EMERGENCY_ATTEMPT_EVENT:
                current_attempt = lease_id is None or event.get("lease_id") == lease_id
                started = validated = normal_end = False
                continue
            if not current_attempt:
                continue
            if normal_end:
                normal_end = False
            if kind == "agent_start":
                started = True
                validated = False
            elif kind == "outputs_validation" and started:
                validated = event.get("missing") == [] and event.get("violations") == []
            elif kind == "agent_end":
                normal_end = (
                    started and validated and not event.get("reason") and not event.get("error")
                )
    return current_attempt and normal_end


def validate_scanned_events(events: Path, original: int, recovered: bool) -> None:
    """Fail closed on raw survivors or completion proof lost during compression."""
    if original == 0 and events.is_file() and events.stat().st_size > 0:
        raise RuntimeError(f"pi events scan failed; the raw events file survived: {events}")
    if recovered and not completed_emergency_events(events):
        raise RuntimeError("recovered emergency events lost completion evidence during compression")


def recover_completed_run(task: UploadTask, job_dir: Path, run_dir: Path) -> Path:
    """Return the primary run dir or a gated emergency-events staging dir."""
    if run_dir.is_dir() or task.exit_code != 0:
        return run_dir
    if any(not (job_dir / PurePosixPath(name)).is_file() for name in task.expected_outputs):
        return run_dir
    return copy_completed_emergency_events(task) or run_dir


def copy_completed_emergency_events(task: UploadTask) -> Path | None:
    """Stage only the latest matching lease's complete events for delivery."""
    incident = incident_dir(task.execution_id, task.node_key)
    if incident is None:
        return None
    source = incident / EMERGENCY_EVENTS_FILENAME
    if not source.is_file() or not completed_emergency_events(source, lease_id=task.lease_id):
        return None
    target = incident / "recovered"
    target.mkdir(parents=True, exist_ok=True)
    with (
        source.open(encoding="utf-8", errors="replace") as src,
        (target / "events.jsonl").open("w", encoding="utf-8") as dst,
    ):
        selected = False
        for line in src:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = None
            if isinstance(event, dict) and event.get("type") == EMERGENCY_ATTEMPT_EVENT:
                selected = event.get("lease_id") == task.lease_id
                dst.seek(0)
                dst.truncate()
                continue
            if selected:
                dst.write(line)
    return target


def scanned_stderr_tail(
    task: UploadTask, evidence_dir: Path, scanned_tail: bytes, scanned_original: int
) -> bytes:
    """Keep the scanned verdict when only the stderr anchor vanishes afterwards."""
    try:
        return stderr_tail_for_run(evidence_dir, scanned_tail)
    except OSError as exc:
        if scanned_original <= 0 or not tree_missing_inside(exc, evidence_dir):
            raise
        print(
            f"run stderr anchor vanished for {task.execution_id}; preserving the scanned verdict",
            flush=True,
        )
        return b""


def from_scan(
    task: UploadTask,
    scanned_result: tuple[dict[str, Any], list[str]] | None,
    prepare: Callable[[UploadTask], tuple[dict[str, Any], Path, list[str]]],
) -> tuple[dict[str, Any], Path, list[str]]:
    """Choose a fresh scan or same-task repack; code/prebuilt lanes stay fresh."""
    if scanned_result is None or task.kind != "process" or task.exec_kind == "code":
        return prepare(task)
    return repack_scanned_result(task, *scanned_result)


def repack_scanned_result(
    task: UploadTask, metadata: dict[str, Any], outputs: list[str]
) -> tuple[dict[str, Any], Path, list[str]]:
    """Repack an agent result without rescanning evidence on direct-upload fallback."""
    archive = task.execution_dir / "result.tar.gz"
    job_dir = task.execution_dir / "job"
    run_dir = job_dir / "runs" / task.node_key / "worker"
    evidence_dir = task.scanned_evidence_dir or run_dir
    write_agent_archive(task, archive, job_dir, run_dir, evidence_dir, outputs, True)
    return dict(metadata), archive, list(outputs)
