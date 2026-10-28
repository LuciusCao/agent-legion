"""Non-secret image/runtime metadata and sanitized measurement logs."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.stress.worker_resource_measurement import sanitize_log_text


def _command(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def collect_host_metadata(container: str, image: str, velites: Path) -> dict[str, Any]:
    """Collect only non-secret identity fields, never the container/image environment."""
    image_fields = _command(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}|{{json .Os}}|{{json .Architecture}}",
            image,
        ]
    ).split("|")
    container_image = json.loads(
        _command(["docker", "inspect", "--format", "{{json .Image}}", container])
    )
    velites_version = _command(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{velites}:/tmp/velites:ro",
            "agent-legion-host:measurement",
            "/tmp/velites",
            "--version",
        ]
    )
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "kernel": platform.release(),
        "kernel_version": platform.version(),
        "os": platform.system(),
        "arch": platform.machine(),
        "cgroup_version": 2,
        "cgroup_filesystem": (
            "docker-desktop-sidecar"
            if platform.system() == "Darwin"
            else _command(["stat", "-fc", "%T", "/sys/fs/cgroup"])
        ),
        "docker_server": json.loads(
            _command(["docker", "version", "--format", "{{json .Server}}"])
        ),
        "worker_container": container,
        "worker_container_image_id": container_image,
        "worker_image": {
            "id": json.loads(image_fields[0]),
            "repo_tags": json.loads(image_fields[1]),
            "repo_digests": json.loads(image_fields[2]),
            "os": json.loads(image_fields[3]),
            "architecture": json.loads(image_fields[4]),
        },
        "velites": {
            "sha256": hashlib.sha256(velites.read_bytes()).hexdigest(),
            "version": velites_version,
        },
    }


def write_sanitized_logs(run: Any, iteration_dir: Path) -> None:
    for service in ("host", "worker"):
        text = run.compose("logs", "--no-color", service, capture=True)
        (iteration_dir / f"{service}.log").write_text(
            sanitize_log_text(text, run.secrets) + "\n", encoding="utf-8"
        )
    if run.control_token:
        response = run._worker_request("GET", "/api/logs?limit=500", run.control_token)
        lines = response.json().get("lines", [])
        (iteration_dir / "worker-control.log").write_text(
            sanitize_log_text("\n".join(lines), run.secrets) + "\n", encoding="utf-8"
        )
    if run.cgroup_reader_id:
        result = subprocess.run(
            ["docker", "logs", run.cgroup_reader_id],
            check=False,
            capture_output=True,
            text=True,
            env=run.env,
        )
        (iteration_dir / "cgroup-reader.log").write_text(
            sanitize_log_text(result.stdout + result.stderr, run.secrets) + "\n",
            encoding="utf-8",
        )


def finalize_measurement_run(
    run: Any, output_dir: Path, reports: list[dict[str, Any]], write_report: Any
) -> dict[str, Any]:
    report = write_report(output_dir, reports)
    try:
        project = run.project
        if not project or run.env.get("COMPOSE_PROJECT_NAME") != project:
            raise RuntimeError("measurement project identity changed before volume cleanup")
        config = json.loads(run.compose("config", "--format", "json", capture=True))
        if not isinstance(config, dict) or config.get("name") != project:
            raise RuntimeError("compose project identity mismatch before volume cleanup")
        run.compose("down", "--volumes", "--remove-orphans")
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        report["success"] = False
        report["cleanup_error"] = sanitize_log_text(f"{type(exc).__name__}: {exc}", run.secrets)
        (output_dir / "run-report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return report
