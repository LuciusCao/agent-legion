from __future__ import annotations

import os
import subprocess
import time
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


def _use_root_image(target: Path, project: str, tmp_path: Path) -> None:
    root_image = f"agent-legion-worker:{project}-root"
    subprocess.run(
        ["docker", "build", "-t", root_image, "-f", "-", str(tmp_path)],
        input="FROM ghcr.io/luciuscao/agent-legion-worker:0.8.2\nUSER 0:0\n",
        text=True,
        check=True,
        capture_output=True,
    )
    env_path = target / ".env"
    updated = env_path.read_text(encoding="utf-8").replace(
        "ghcr.io/luciuscao/agent-legion-worker:0.8.2", root_image
    )
    env_path.write_text(updated, encoding="utf-8")


def _cleanup_project(target: Path, project: str, compose: str = "docker-compose.yaml") -> None:
    subprocess.run(["docker", "compose", "-f", compose, "down"], cwd=target, capture_output=True)
    subprocess.run(
        ["docker", "volume", "rm", "-f", f"{project}_worker-control", f"{project}_worker-data"],
        capture_output=True,
    )


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
    tmp_path: Path,
    target: Path,
    *,
    version: str = "0.8.2",
    extra_env: dict[str, str] | None = None,
    compose_source: Path | None = None,
    migration_source: Path | None = None,
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
        '  */scripts/migrate-worker-volumes.sh) cp "$FAKE_MIGRATION" "$out" ;;\n'
        '  *) exec /usr/bin/curl -fsSL -o "$out" "$url" ;;\n'
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
            "FAKE_COMPOSE": str(compose_source or target / "docker-compose.yaml"),
            "FAKE_MIGRATION": str(migration_source or MIGRATOR),
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
            "--user",
            "0:0",
            "--entrypoint",
            "python3",
            "-v",
            f"{volume}:/control",
            "ghcr.io/luciuscao/agent-legion-worker:0.8.2",
            "-c",
            f"from pathlib import Path; path = Path('/control/worker.yaml'); path.write_text({document!r}, encoding='utf-8'); path.chmod(0o600)",
        ],
        check=True,
        capture_output=True,
    )
    return volume


def test_installer_real_docker_fresh_and_idempotent(tmp_path: Path) -> None:
    target, project = _real_project(tmp_path, "real-fresh")
    try:
        result = _run_real_installer(tmp_path, target)
        assert result.returncode == 0, result.stderr
        assert _env(target)["AGENT_WORKER_RESOURCE_LIMITS_MODE"] == "default"
        before = (target / ".env").read_bytes()
        result = _run_real_installer(tmp_path, target)
        assert result.returncode == 0, result.stderr
        assert (target / ".env").read_bytes() == before
    finally:
        _cleanup_project(target, project)


@pytest.mark.parametrize("document", ["max_concurrency: 11\n", "max_code_concurrency: 1\n"])
def test_installer_real_control_volume_rejects_default_without_writing(
    tmp_path: Path, document: str
) -> None:
    target, project = _real_project(tmp_path, "real-control")
    (target / "worker.yaml").write_text("max_concurrency: 10\n", encoding="utf-8")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    before_compose = (target / "docker-compose.yaml").read_bytes()
    _write_control_volume(project, document)
    try:
        result = _run_real_installer(tmp_path, target)
        assert result.returncode == 1 and "测量 envelope" in result.stderr
        assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"
        assert (target / "docker-compose.yaml").read_bytes() == before_compose
    finally:
        _cleanup_project(target, project)


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
    _write_control_volume(project, "max_concurrency: 11\n")
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
        _cleanup_project(target, project)


MIGRATOR = REPO_ROOT / "scripts/migrate-worker-volumes.sh"


def _nonroot_project(tmp_path: Path, form: str) -> tuple[Path, str]:
    target, project = _real_project(tmp_path, form)
    (target / ".env").write_text("AGENT_WORKER_UI_PORT=0\n", encoding="utf-8")
    (target / "velites-bin/velites").write_text(
        """#!/bin/sh
if [ "$1" = models ]; then
  exec python3 -c 'import json,os; data=json.load(open(os.path.expanduser("~/.velites/models.json"), encoding="utf-8")); print(json.dumps(data["models"]))'
fi
if [ "$1" = --version ]; then echo velites-nonroot-test; exit 0; fi
exit 1
""",
        encoding="utf-8",
    )
    (target / "velites-bin/velites").chmod(0o755)
    (target / "velites-config").mkdir(exist_ok=True)
    (target / "pi-config").mkdir(mode=0o700, exist_ok=True)
    (target / "velites-config/models.json").write_text(
        '{"models":[{"provider":"stub","model":"nonroot-ok"}]}', encoding="utf-8"
    )
    (target / "velites-config/models.json").chmod(0o600)
    return target, project


def _compose(target, *args, compose="docker-compose.yaml", check=True):
    return subprocess.run(
        ["docker", "compose", "-f", compose, *args],
        cwd=target,
        capture_output=True,
        text=True,
        check=check,
    )


def _wait_worker_api(target: Path, compose: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        probe = _compose(
            target,
            "exec",
            "-T",
            "worker",
            "python3",
            "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=2)",
            compose=compose,
            check=False,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.5)
    logs = _compose(target, "logs", "--tail", "80", "worker", compose=compose)
    raise AssertionError(f"Worker API did not become ready:\n{logs.stdout}\n{logs.stderr}")


def _seed_pending_upload(project: str) -> None:
    marker = {
        "version": 1,
        "execution_id": "exec-pending",
        "lease_id": "lease-pending",
        "node_key": "node_a",
        "status_fields": {"node_key": "node_a"},
        "kind": "prebuilt",
        "exit_code": 1,
        "expected_outputs": [],
        "command": [],
        "prebuilt_metadata": {"status": "failed", "exit_code": 1, "error_message": "pending"},
    }
    script = f"""from pathlib import Path
import json, os
control = Path("/control")
data = Path("/data")
(data / "exec-pending").mkdir(parents=True, exist_ok=True)
config = {{"host_url": "http://127.0.0.1:9", "worker_id": "pending", "work_root": "/var/lib/agent-legion-worker"}}
(control / "worker.yaml").write_text(json.dumps(config), encoding="utf-8")
(control / "control_token").write_text("root-owned-control-token\\n", encoding="utf-8")
(data / "exec-pending/upload_pending.json").write_text(json.dumps({marker!r}), encoding="utf-8")
for root in (control, data):
    for directory, dirs, files in os.walk(root):
        for name in [*dirs, *files]:
            os.chmod(Path(directory) / name, 0o600 if name in files else 0o700)
"""
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--entrypoint",
            "python3",
            "-v",
            f"{project}_worker-control:/control",
            "-v",
            f"{project}_worker-data:/data",
            "ghcr.io/luciuscao/agent-legion-worker:0.8.2",
            "-c",
            script,
        ],
        check=True,
        capture_output=True,
    )


def _prepare_no_build(tmp_path: Path, form: str) -> tuple[Path, str]:
    _ensure_real_worker_image()
    target, project = _nonroot_project(tmp_path, form)
    (target / "worker.yaml").write_text(
        "host_url: http://127.0.0.1:9\nworker_id: no-build\nwork_root: /var/lib/agent-legion-worker\n",
        encoding="utf-8",
    )
    (target / "worker.yaml").chmod(0o600)
    with (target / ".env").open("a", encoding="utf-8") as stream:
        stream.write(
            f"AGENT_WORKER_UID={os.getuid()}\nAGENT_WORKER_GID={os.getgid()}\n"
            "AGENT_WORKER_IMAGE=ghcr.io/luciuscao/agent-legion-worker:0.8.2\n"
        )
        if form != "standalone":
            stream.write(
                f"AGENT_WORKER_CONFIG={target / 'worker.yaml'}\n"
                f"VELITES_BIN={target / 'velites-bin/velites'}\n"
                f"VELITES_CONFIG_DIR={target / 'velites-config'}\n"
                f"PI_CONFIG_DIR={target / 'pi-config'}\n"
            )
        if form == "zero-runtime-pull":
            stream.write("AGENT_WORKER_EXPECT_RUNTIMES=\n")
    if form != "standalone":
        base = (REPO_ROOT / "deploy/compose.worker.yaml").read_text(encoding="utf-8")
        (target / "docker-compose.base.yaml").write_text(
            base.replace("name: agent-legion-worker", f"name: {project}", 1).replace(
                ":8787:8787", ":0:8787", 1
            ),
            encoding="utf-8",
        )
        pull = (REPO_ROOT / "deploy/compose.worker.pull.example.yaml").read_text(encoding="utf-8")
        (target / "docker-compose.pull.yaml").write_text(
            pull.replace("0.6.0", "0.8.2"), encoding="utf-8"
        )
        compose_args = ["-f", "docker-compose.base.yaml"]
        if form == "zero-runtime-pull":
            zero = (REPO_ROOT / "deploy/compose.worker.zero-runtime.yaml").read_text(
                encoding="utf-8"
            )
            (target / "docker-compose.zero.yaml").write_text(zero, encoding="utf-8")
            compose_args += ["-f", "docker-compose.zero.yaml"]
        compose_args += ["-f", "docker-compose.pull.yaml"]
    else:
        compose_args = ["-f", "docker-compose.yaml"]
    resolved = subprocess.run(
        ["docker", "compose", *compose_args, "config"],
        cwd=target,
        capture_output=True,
        text=True,
        check=True,
    )
    (target / "docker-compose.resolved.yaml").write_text(resolved.stdout, encoding="utf-8")
    return target, project


def _run_migrator(target: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = [
        "sh",
        str(MIGRATOR),
        "--uid",
        str(os.getuid()),
        "--gid",
        str(os.getgid()),
        "--",
        "-f",
        "docker-compose.resolved.yaml",
    ]
    return subprocess.run(command, cwd=target, check=check, capture_output=True, text=True)


@pytest.mark.parametrize("form", ["standalone", "repo-pull", "zero-runtime-pull"])
def test_no_build_custom_uid_fresh_starts_and_writes_control_token(tmp_path, form):
    target, project = _prepare_no_build(tmp_path, form)
    try:
        if form == "standalone":
            _compose(target, "up", "-d", compose="docker-compose.resolved.yaml")
            deadline = time.monotonic() + 30
            while True:
                failure = _compose(
                    target,
                    "logs",
                    "--tail",
                    "40",
                    "worker",
                    compose="docker-compose.resolved.yaml",
                    check=False,
                )
                if "PermissionError" in failure.stdout + failure.stderr:
                    break
                assert time.monotonic() < deadline, failure.stdout + failure.stderr
                time.sleep(0.2)
            _compose(target, "down", compose="docker-compose.resolved.yaml")
        _run_migrator(target)
        _compose(target, "up", "-d", compose="docker-compose.resolved.yaml")
        _wait_worker_api(target, "docker-compose.resolved.yaml")
        result = _compose(
            target,
            "exec",
            "-T",
            "worker",
            "sh",
            "-c",
            "id -u; stat -c '%u:%g:%a' /var/lib/agent-legion-worker-control/control_token; workerctl status",
            compose="docker-compose.resolved.yaml",
        )
        lines = result.stdout.splitlines()
        assert lines[0] == str(os.getuid())
        assert lines[1] == f"{os.getuid()}:{os.getgid()}:600"
        assert "服务: running" in result.stdout
    finally:
        _cleanup_project(target, project, "docker-compose.resolved.yaml")


@pytest.mark.parametrize("key", ["AGENT_WORKER_UID", "AGENT_WORKER_GID"])
@pytest.mark.parametrize("source", ["env", "dotenv"])
def test_installer_rejects_chown_sentinel_without_partial_application(
    tmp_path: Path, key: str, source: str
) -> None:
    target, project = _real_project(tmp_path, "sentinel")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    (target / "docker-compose.yaml").write_text("old-compose\n", encoding="utf-8")
    extra_env = {key: "4294967295"} if source == "env" else {}
    try:
        if source == "dotenv":
            with (target / ".env").open("a", encoding="utf-8") as stream:
                stream.write(f"{key}=4294967295\n")
        result = _run_real_installer(tmp_path, target, extra_env=extra_env)
        assert result.returncode == 1 and "不修改哨兵" in result.stderr
        assert (target / "docker-compose.yaml").read_bytes() == b"old-compose\n"
        expected_env = "AGENT_WORKER_IMAGE=old\n"
        if source == "dotenv":
            expected_env += f"{key}=4294967295\n"
        assert (target / ".env").read_text(encoding="utf-8") == expected_env
        volumes = subprocess.run(
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        assert f"{project}_worker-control" not in volumes
        assert f"{project}_worker-data" not in volumes
    finally:
        _cleanup_project(target, project)


def test_pending_upload_restores_and_delivers_after_volume_migration(tmp_path: Path) -> None:
    target, project = _prepare_no_build(tmp_path, "standalone")
    server = f"m127-upload-{uuid.uuid4().hex[:12]}"
    try:
        _seed_pending_upload(project)
        _run_migrator(target)
        server_script = """import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        Path("/control/received.json").write_text(json.dumps({"path": self.path, "size": len(body)}))
        self.send_response(204)
        self.end_headers()
    def log_message(self, *args):
        pass
ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
"""
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                server,
                "--network",
                f"{project}_default",
                "--network-alias",
                "fake-host",
                "-v",
                f"{project}_worker-control:/control",
                "--user",
                "0:0",
                "--entrypoint",
                "python3",
                "ghcr.io/luciuscao/agent-legion-worker:0.8.2",
                "-c",
                server_script,
            ],
            check=True,
            capture_output=True,
        )
        restore_script = """import threading
from pathlib import Path
from worker.host.client import Client
from worker.status import ExecutionStatusReporter
from worker.upload.queue import UploadQueue
client = Client("http://fake-host:8000", timeout=5, transfer_timeout=5)
client.session.trust_env = False
stop = threading.Event()
timer = threading.Timer(30, stop.set)
timer.start()
queue = UploadQueue(client, ExecutionStatusReporter(None), heartbeat_interval=3600, stop=stop)
work = Path("/var/lib/agent-legion-worker")
try:
    assert queue.restore(work) == 1
    queue.shutdown()
finally:
    timer.cancel()
assert not (work / "exec-pending").exists()
"""
        restored = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "docker-compose.resolved.yaml",
                "run",
                "--rm",
                "--no-deps",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--entrypoint",
                "python3",
                "worker",
                "-c",
                restore_script,
            ],
            cwd=target,
            check=False,
            capture_output=True,
            text=True,
        )
        if restored.returncode:
            logs = subprocess.run(["docker", "logs", server], capture_output=True, text=True)
            raise AssertionError(
                f"restore stdout={restored.stdout}\nstderr={restored.stderr}\n"
                f"server={logs.stdout}\n{logs.stderr}"
            )
        evidence = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0:0",
                "--entrypoint",
                "python3",
                "-v",
                f"{project}_worker-control:/control",
                "ghcr.io/luciuscao/agent-legion-worker:0.8.2",
                "-c",
                "import json; from pathlib import Path; "
                "data = json.loads(Path('/control/received.json').read_text()); "
                "assert data['path'].endswith('/exec-pending/result'), data; "
                "assert data['size'] > 0, data; print(data)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "exec-pending/result" in evidence.stdout
    finally:
        subprocess.run(["docker", "rm", "-f", server], capture_output=True)
        _cleanup_project(target, project, "docker-compose.resolved.yaml")


def test_root_current_to_nonroot_target_starts_and_writes_token(tmp_path: Path) -> None:
    target, project = _prepare_no_build(tmp_path, "root-current")
    target_source = tmp_path / "target-nonroot.yaml"
    target_source.write_text((target / "docker-compose.yaml").read_text(encoding="utf-8"))
    current = (target / "docker-compose.yaml").read_text(encoding="utf-8")
    (target / "docker-compose.yaml").write_text(
        current.replace("    user: ${AGENT_WORKER_UID:-10001}:${AGENT_WORKER_GID:-10001}\n", ""),
        encoding="utf-8",
    )
    try:
        _seed_pending_upload(project)
        result = _run_real_installer(tmp_path, target, compose_source=target_source)
        assert result.returncode == 0, result.stderr
        _compose(target, "up", "-d")
        _wait_worker_api(target, "docker-compose.yaml")
        status = _compose(
            target,
            "exec",
            "-T",
            "worker",
            "sh",
            "-c",
            "stat -c '%u:%g:%a' /var/lib/agent-legion-worker-control/control_token; workerctl status",
        )
        assert status.stdout.splitlines()[0] == f"{os.getuid()}:{os.getgid()}:600"
        assert "服务: running" in status.stdout
    finally:
        _cleanup_project(target, project)


def test_root_current_rejects_non_0600_state_without_touching_running_worker(
    tmp_path: Path,
) -> None:
    target, project = _prepare_no_build(tmp_path, "root-state-mode")
    target_source = tmp_path / "target-nonroot.yaml"
    target_source.write_text((target / "docker-compose.yaml").read_text(encoding="utf-8"))
    current = (target / "docker-compose.yaml").read_text(encoding="utf-8")
    (target / "docker-compose.yaml").write_text(
        current.replace("    user: ${AGENT_WORKER_UID:-10001}:${AGENT_WORKER_GID:-10001}\n", ""),
        encoding="utf-8",
    )
    try:
        _use_root_image(target, project, tmp_path)
        _seed_pending_upload(project)
        _compose(target, "up", "-d")
        _wait_worker_api(target, "docker-compose.yaml")
        _compose(
            target,
            "exec",
            "-T",
            "worker",
            "chmod",
            "644",
            "/var/lib/agent-legion-worker-control/worker.yaml",
        )
        before_compose = (target / "docker-compose.yaml").read_bytes()
        before_env = (target / ".env").read_bytes()
        result = _run_real_installer(tmp_path, target, compose_source=target_source)
        assert result.returncode == 1 and "0600" in result.stderr
        assert (target / "docker-compose.yaml").read_bytes() == before_compose
        assert (target / ".env").read_bytes() == before_env
        running = _compose(
            target,
            "exec",
            "-T",
            "worker",
            "sh",
            "-c",
            "id -u; stat -c '%u:%g:%a' /var/lib/agent-legion-worker-control/worker.yaml; workerctl status",
        )
        assert running.stdout.splitlines()[:2] == ["0", "0:0:644"]
        assert "服务: running" in running.stdout
    finally:
        _cleanup_project(target, project)


def test_nonroot_current_to_legacy_target_rolls_back_without_helper(tmp_path: Path) -> None:
    target, project = _prepare_no_build(tmp_path, "nonroot-current")
    legacy_source = tmp_path / "target-root.yaml"
    current = (target / "docker-compose.yaml").read_text(encoding="utf-8")
    legacy_source.write_text(
        current.replace("    user: ${AGENT_WORKER_UID:-10001}:${AGENT_WORKER_GID:-10001}\n", ""),
        encoding="utf-8",
    )
    try:
        _seed_pending_upload(project)
        _run_migrator(target)
        result = _run_real_installer(
            tmp_path,
            target,
            compose_source=legacy_source,
            migration_source=tmp_path / "helper-does-not-exist",
        )
        assert result.returncode == 0, result.stderr
        assert "旧 root Compose 无需 UID 卷迁移" in result.stdout
        _use_root_image(target, project, tmp_path)
        _compose(target, "up", "-d")
        _wait_worker_api(target, "docker-compose.yaml")
        rollback = _compose(
            target,
            "exec",
            "-T",
            "worker",
            "sh",
            "-c",
            "id -u; cat /var/lib/agent-legion-worker-control/control_token; workerctl status",
        )
        assert rollback.stdout.splitlines()[0] == "0"
        assert "root-owned-control-token" in rollback.stdout
        assert "服务: running" in rollback.stdout
    finally:
        _cleanup_project(target, project)


def test_installer_rejects_changed_volume_identity_before_chown(tmp_path: Path) -> None:
    target, project = _prepare_no_build(tmp_path, "identity-mismatch")
    target_source = tmp_path / "target-other-project.yaml"
    current = (target / "docker-compose.yaml").read_text(encoding="utf-8")
    target_source.write_text(current.replace(project, f"{project}-other", 1), encoding="utf-8")
    before = (target / "docker-compose.yaml").read_bytes()
    try:
        _seed_pending_upload(project)
        result = _run_real_installer(tmp_path, target, compose_source=target_source)
        assert result.returncode == 1 and "身份不一致" in result.stderr
        assert (target / "docker-compose.yaml").read_bytes() == before
    finally:
        _cleanup_project(target, project)


@pytest.mark.parametrize("kind", ["bootstrap", "state"])
def test_direct_migration_rejects_non_0600_worker_yaml_before_chown(
    tmp_path: Path, kind: str
) -> None:
    target, project = _prepare_no_build(tmp_path, f"bad-mode-{kind}")
    try:
        _seed_pending_upload(project)
        if kind == "bootstrap":
            (target / "worker.yaml").chmod(0o644)
        else:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--user",
                    "0:0",
                    "--entrypoint",
                    "chmod",
                    "-v",
                    f"{project}_worker-control:/control",
                    "ghcr.io/luciuscao/agent-legion-worker:0.8.2",
                    "644",
                    "/control/worker.yaml",
                ],
                check=True,
                capture_output=True,
            )
        result = _run_migrator(target, check=False)
        assert result.returncode == 1 and "0600" in result.stderr
    finally:
        _cleanup_project(target, project, "docker-compose.resolved.yaml")


def test_installer_uses_staged_binary_when_bind_source_is_directory(tmp_path: Path) -> None:
    target, project = _real_project(tmp_path, "binary-directory")
    (target / "velites-bin/velites").unlink()
    (target / "velites-bin/velites").mkdir()
    (target / "velites-bin/.velites-version").write_text("0.0.0", encoding="utf-8")
    try:
        result = _run_real_installer(tmp_path, target)
        assert result.returncode == 0, result.stderr
        binary = target / "velites-bin/velites"
        assert binary.is_file() and not binary.is_symlink()
        assert binary.stat().st_mode & 0o777 == 0o755
        assert (target / "velites-bin/.velites-version").read_text(encoding="utf-8") == "0.5.9\n"
    finally:
        _cleanup_project(target, project)
