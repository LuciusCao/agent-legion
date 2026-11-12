"""Controlled backlog evidence and reporting helpers for Worker measurement."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.stress.worker_resource_reporting import collect_host_metadata


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


def require_sandbox_ready(run: Any) -> None:
    run.compose(
        *[
            "exec",
            "-T",
            "worker",
            "/app/data/bin/velites",
            "sandbox",
            "wrap",
            "--cwd",
            "/tmp",
            "--",
            "/bin/true",
        ]
    )


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
        "evidence_scope": (
            "docker-desktop-functional-only"
            if platform.system() == "Darwin"
            else "native-linux-measurement"
        ),
        "shipped_envelope": {
            "user": "root(image default)",
            "cap_add": ["SYS_ADMIN"],
            "seccomp": "unconfined",
            "mem_limit": None,
            "pids_limit": None,
        },
        "functional_cap_override": run.env.get("MEASUREMENT_NET_ADMIN", "SYS_ADMIN"),
        "functional_apparmor_override": run.env.get("MEASUREMENT_APPARMOR", "apparmor:unconfined"),
        "shipped_apparmor": "docker-default",
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
