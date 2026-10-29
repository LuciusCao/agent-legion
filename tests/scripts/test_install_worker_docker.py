from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db
REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "install-worker.sh"


def _target(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    (target / "velites-bin").mkdir(parents=True)
    (target / "velites-bin/velites").write_text("binary", encoding="utf-8")
    (target / "velites-bin/.velites-version").write_text("0.5.9", encoding="utf-8")
    return target


def _env(target: Path) -> dict[str, str]:
    return {
        key: value
        for line in (target / ".env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, value in [line.split("=", 1)]
    }


def _ensure_real_worker_image() -> None:
    source = "agent-legion-worker:measurement"
    inspect = subprocess.run(
        ["docker", "image", "inspect", source], capture_output=True, check=False
    )
    if inspect.returncode != 0:
        subprocess.run(
            ["docker", "build", "--target", "worker", "-t", source, "."],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
    for version in ("0.8.2", "0.8.3"):
        target = f"ghcr.io/luciuscao/agent-legion-worker:{version}"
        if (
            subprocess.run(
                ["docker", "image", "inspect", target], capture_output=True, check=False
            ).returncode
            != 0
        ):
            subprocess.run(["docker", "tag", source, target], check=True, capture_output=True)


def _real_project(tmp_path: Path, name: str) -> tuple[Path, str]:
    target = _target(tmp_path, name)
    project = f"m126-test-{uuid.uuid4().hex[:12]}"
    compose = (REPO_ROOT / "deploy/compose.worker.standalone.yaml").read_text(encoding="utf-8")
    compose = compose.replace("name: agent-legion-worker", f"name: {project}", 1)
    (target / "docker-compose.yaml").write_text(compose, encoding="utf-8")
    return target, project


def _run_real_installer(
    tmp_path: Path, target: Path, *, version: str = "0.8.2", extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    _ensure_real_worker_image()
    fake_bin = tmp_path / "real-fake-bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "curl").write_text(
        "#!/bin/sh\n"
        "out=; url=\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = -o ]; then out="$2"; shift 2; else url="$1"; shift; fi\n'
        "done\n"
        'case "$url" in\n'
        '  */deploy/compose.worker.standalone.yaml) cp "$FAKE_COMPOSE" "$out" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (fake_bin / "curl").chmod(0o755)
    env = os.environ.copy()
    for key in (
        "AGENT_WORKER_RESOURCE_LIMITS_MODE",
        "AGENT_WORKER_MEM_LIMIT",
        "AGENT_WORKER_PIDS_LIMIT",
    ):
        env.pop(key, None)
    env.update(extra_env or {})
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_COMPOSE": str(target / "docker-compose.yaml"),
            "AGENT_WORKER_VERSION": version,
            "AGENT_WORKER_COMPOSE_REF": "test-ref",
        }
    )
    return subprocess.run(
        [str(INSTALLER), "--target", str(target), "--no-up"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_control_volume(project: str, document: str) -> str:
    volume = f"{project}_worker-control"
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python3",
            "-v",
            f"{volume}:/control",
            "ghcr.io/luciuscao/agent-legion-worker:0.8.2",
            "-c",
            f"from pathlib import Path; Path('/control/worker.yaml').write_text({document!r}, encoding='utf-8')",
        ],
        check=True,
        capture_output=True,
    )
    return volume


def test_installer_real_docker_fresh_and_idempotent(tmp_path: Path) -> None:
    target, _project = _real_project(tmp_path, "real-fresh")
    result = _run_real_installer(tmp_path, target)
    assert result.returncode == 0, result.stderr
    assert _env(target)["AGENT_WORKER_RESOURCE_LIMITS_MODE"] == "default"
    before = (target / ".env").read_bytes()
    result = _run_real_installer(tmp_path, target)
    assert result.returncode == 0, result.stderr
    assert (target / ".env").read_bytes() == before


@pytest.mark.parametrize("document", ["max_concurrency: 11\n", "max_code_concurrency: 1\n"])
def test_installer_real_control_volume_rejects_default_without_writing(
    tmp_path: Path, document: str
) -> None:
    target, project = _real_project(tmp_path, "real-control")
    (target / "worker.yaml").write_text("max_concurrency: 10\n", encoding="utf-8")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    before_compose = (target / "docker-compose.yaml").read_bytes()
    volume = _write_control_volume(project, document)
    try:
        result = _run_real_installer(tmp_path, target)
        assert result.returncode == 1 and "测量 envelope" in result.stderr
        assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"
        assert (target / "docker-compose.yaml").read_bytes() == before_compose
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)


def test_installer_real_control_volume_explicit_upgrade_rollback_and_invalid_yaml(
    tmp_path: Path,
) -> None:
    target, project = _real_project(tmp_path, "real-explicit")
    (target / "worker.yaml").write_text("max_concurrency: 10\n", encoding="utf-8")
    (target / ".env").write_text(
        "AGENT_WORKER_RESOURCE_LIMITS_MODE=explicit\n"
        "AGENT_WORKER_MEM_LIMIT=512m\n"
        "AGENT_WORKER_PIDS_LIMIT=256\n",
        encoding="utf-8",
    )
    volume = _write_control_volume(project, "max_concurrency: 11\n")
    try:
        for version in ("0.8.3", "0.8.2"):
            result = _run_real_installer(tmp_path, target, version=version)
            assert result.returncode == 0, result.stderr
            assert _env(target)["AGENT_WORKER_MEM_LIMIT"] == "512m"
            assert _env(target)["AGENT_WORKER_PIDS_LIMIT"] == "256"
            assert _env(target)["AGENT_WORKER_IMAGE"].endswith(f":{version}")
        before_env = (target / ".env").read_bytes()
        before_compose = (target / "docker-compose.yaml").read_bytes()
        _write_control_volume(project, "max_concurrency: [11\n")
        result = _run_real_installer(tmp_path, target)
        assert result.returncode == 1 and "可靠解析" in result.stderr
        assert (target / ".env").read_bytes() == before_env
        assert (target / "docker-compose.yaml").read_bytes() == before_compose
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)
