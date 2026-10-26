"""Fail-closed upload-channel and backlog evidence for Worker measurement."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from scripts.stress.worker_resource_measurement import (
    collect_host_metadata,
    sanitize_log_text,
)
from server.app.services.job_artifact_gzip import content_stream
from server.app.storage import ObjectStorage

_HASH = re.compile(r"^[0-9a-f]{64}$")


def require_direct_outputs(
    execution: Mapping[str, Any], expected_names: Iterable[str], workspace_id: str
) -> dict[str, dict[str, Any]]:
    """Require every declared output to carry its complete direct staging ref.

    A terminal/done row and a legacy CAS string are not upload-channel evidence.
    """
    outcome_raw = execution.get("outcome_json")
    outcome = json.loads(outcome_raw) if isinstance(outcome_raw, str) else outcome_raw
    if not isinstance(outcome, Mapping):
        raise RuntimeError(f"execution {execution.get('execution_id')} has no final outcome")
    outputs = outcome.get("output_artifacts")
    expected = set(expected_names)
    if not expected or not isinstance(outputs, Mapping) or set(outputs) != expected:
        raise RuntimeError(
            f"execution {execution.get('execution_id')} output set mismatch: "
            f"expected {sorted(expected)}, got {sorted(outputs) if isinstance(outputs, Mapping) else type(outputs).__name__}"
        )
    refs: dict[str, dict[str, Any]] = {}
    for name in sorted(expected):
        ref = outputs[name]
        if not isinstance(ref, Mapping):
            raise RuntimeError(
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
            raise RuntimeError(
                f"execution {execution['execution_id']} output {name} has wrong staging key"
            )
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError(
                f"execution {execution['execution_id']} output {name} has invalid size"
            )
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise RuntimeError(
                f"execution {execution['execution_id']} output {name} has invalid hash"
            )
        refs[name] = {
            "storage_key": storage_key,
            "size_bytes": size,
            "content_hash": digest,
        }
    return refs


def verify_authority_object(
    storage: ObjectStorage,
    *,
    execution_id: str,
    name: str,
    ref: Mapping[str, Any],
    row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Cross-check direct ref, promoted manifest row, HEAD size and content digest."""
    if row is None:
        raise RuntimeError(f"execution {execution_id} output {name} has no job_artifacts row")
    authority_key = row.get("storage_key")
    if not isinstance(authority_key, str) or not authority_key.startswith("jobs/"):
        raise RuntimeError(f"execution {execution_id} output {name} has no promoted authority key")
    if int(row.get("size_bytes", -1)) != int(ref["size_bytes"]):
        raise RuntimeError(f"execution {execution_id} output {name} manifest size mismatch")
    if row.get("content_hash") != ref["content_hash"]:
        raise RuntimeError(f"execution {execution_id} output {name} manifest hash mismatch")
    head = storage.head_object(authority_key)
    if head is None:
        raise RuntimeError(f"execution {execution_id} output {name} authority object is missing")
    if head.size_bytes != int(ref["size_bytes"]):
        raise RuntimeError(f"execution {execution_id} output {name} object size mismatch")
    digest = hashlib.sha256()
    with content_stream(storage, authority_key) as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != ref["content_hash"]:
        raise RuntimeError(f"execution {execution_id} output {name} object hash mismatch")
    return {
        "execution_id": execution_id,
        "output": name,
        "direct_ref": dict(ref),
        "authority_key": authority_key,
        "object_verified": True,
    }


class BacklogEvidence:
    """Require active and queued uploads at the same observation, not a timing guess."""

    def __init__(self) -> None:
        self.observation_count = 0
        self.simultaneous_observation_count = 0
        self.peak_active = 0
        self.peak_queued = 0
        self.first_observed_at: str | None = None
        self.last_observed_at: str | None = None
        self.first_simultaneous_at: str | None = None
        self.last_simultaneous_at: str | None = None
        self.first_simultaneous_monotonic: float | None = None
        self.last_simultaneous_monotonic: float | None = None

    def observe(
        self, status: Mapping[str, Any], *, recorded_at: str, monotonic_seconds: float
    ) -> bool:
        active = status.get("upload_active_count")
        queued = status.get("upload_queued_count")
        for value in (active, queued):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeError("Worker upload status counts must be non-negative integers")
        self.observation_count += 1
        self.peak_active = max(self.peak_active, active)
        self.peak_queued = max(self.peak_queued, queued)
        if self.first_observed_at is None:
            self.first_observed_at = recorded_at
        self.last_observed_at = recorded_at
        simultaneous = active >= 1 and queued >= 1
        if simultaneous:
            self.simultaneous_observation_count += 1
            if self.first_simultaneous_at is None:
                self.first_simultaneous_at = recorded_at
                self.first_simultaneous_monotonic = monotonic_seconds
            self.last_simultaneous_at = recorded_at
            self.last_simultaneous_monotonic = monotonic_seconds
        return simultaneous

    def require_observed(self) -> None:
        if self.simultaneous_observation_count == 0:
            raise RuntimeError(
                "upload backlog was not observed: active>=1 and queued>=1 are both required"
            )

    def summary(self) -> dict[str, Any]:
        self.require_observed()
        return {
            "barrier": "docker compose pause/unpause seaweedfs",
            "observation_count": self.observation_count,
            "simultaneous_observation_count": self.simultaneous_observation_count,
            "peak_active": self.peak_active,
            "peak_queued": self.peak_queued,
            "first_observed_at": self.first_observed_at,
            "last_observed_at": self.last_observed_at,
            "first_simultaneous_at": self.first_simultaneous_at,
            "last_simultaneous_at": self.last_simultaneous_at,
            "first_simultaneous_monotonic": self.first_simultaneous_monotonic,
            "last_simultaneous_monotonic": self.last_simultaneous_monotonic,
        }


def measure_upload_backlog(
    run: Any, session: Any, iteration: str, control_token: str, sampler: Any
) -> dict[str, Any]:
    """Pause only the measurement S3, prove a queue, then always release it."""
    from datetime import UTC, datetime
    from time import monotonic

    evidence = BacklogEvidence()
    try:
        run._worker_request("PUT", "/api/config", control_token, json={"upload_max_concurrency": 1})
        run.compose("pause", "seaweedfs")
        pending = run._create_run(session, "upload-backlog", iteration)

        def observed() -> bool:
            status = run._worker_request("GET", "/api/status", control_token).json()
            active = status.get("upload_active_count")
            queued = status.get("upload_queued_count")
            if isinstance(active, int) and isinstance(queued, int):
                sampler.record_backlog(active=active, queued=queued)
            return evidence.observe(
                status,
                recorded_at=datetime.now(UTC).isoformat(),
                monotonic_seconds=monotonic(),
            )

        try:
            run._wait(
                "simultaneous active and queued uploads",
                observed,
                timeout=run.args.backlog_timeout_seconds,
            )
        except RuntimeError:
            evidence.require_observed()
            raise
    finally:
        try:
            run.compose("unpause", "seaweedfs")
        finally:
            run._worker_request(
                "PUT",
                "/api/config",
                control_token,
                json={"upload_max_concurrency": run.args.upload_concurrency},
            )
    result = evidence.summary()
    result.update(run._await_run(session, "upload-backlog", pending))
    result["barrier_released"] = True
    return result


def ensure_measurement_bucket(run: Any) -> None:
    env = run.env | {
        "AGENT_LEGION_S3_ENDPOINT": f"http://127.0.0.1:{run.args.s3_port}",
        "AGENT_LEGION_S3_PUBLIC_ENDPOINT": f"http://127.0.0.1:{run.args.s3_port}",
        "AGENT_LEGION_S3_BUCKET": "agent-legion-measurement",
        "AGENT_LEGION_S3_ACCESS_KEY": "measurement-access-key",
        "AGENT_LEGION_S3_SECRET_KEY": "measurement-secret-key",
    }
    subprocess.run(
        [sys.executable, "scripts/ensure-s3-bucket.py", str(run.work_dir / "no-secrets.env")],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        check=True,
    )


def write_iteration_metadata(run: Any, iteration_dir: Path, iteration: str, container: str) -> None:
    metadata = collect_host_metadata(container, "agent-legion-worker:measurement", run.velites)
    metadata["measurement"] = {
        "iteration": iteration,
        "shipped_envelope": {
            "user": "root(image default)",
            "cap_add": ["SYS_ADMIN"],
            "seccomp": "unconfined",
            "mem_limit": None,
            "pids_limit": None,
        },
        "envelope_not_a_production_recommendation": True,
        "agent_concurrency": run.args.agent_concurrency,
        "code_concurrency": run.args.code_concurrency,
        "upload_concurrency": run.args.upload_concurrency,
        "jobs_per_wave": run.args.jobs_per_wave,
        "artifact_chars": run.args.artifact_chars,
        "pids_interval_seconds": run.args.pids_interval_ms / 1000,
        "memory_interval_seconds": run.args.memory_interval_ms / 1000,
        "source_commit": run.env.get("GITHUB_SHA"),
        "models_sha256": hashlib.sha256(run.models_path.read_bytes()).hexdigest(),
    }
    (iteration_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_sanitized_logs(run: Any, iteration_dir: Path) -> None:
    for service in ("host", "worker"):
        text = run.compose("logs", "--no-color", service, capture=True)
        (iteration_dir / f"{service}.log").write_text(
            sanitize_log_text(text, run.secrets) + "\n", encoding="utf-8"
        )
