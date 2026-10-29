from __future__ import annotations

import json
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


@pytest.fixture(scope="session")
def nonroot_worker_image() -> str:
    image = "agent-legion-worker:m127-test"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
        subprocess.run(
            ["docker", "build", "--target", "worker", "-t", image, "."],
            cwd=REPO_ROOT,
            check=True,
        )
    subprocess.run(
        ["docker", "tag", image, "ghcr.io/luciuscao/agent-legion-worker:0.8.2"],
        check=True,
    )
    return image


def _with_measurement_apparmor(compose: str) -> str:
    if not os.environ.get("MEASUREMENT_APPARMOR"):
        return compose
    return compose.replace(
        "      - seccomp:unconfined\n",
        "      - seccomp:unconfined\n      - ${MEASUREMENT_APPARMOR}\n",
        1,
    )


def _real_nonroot_project(tmp_path: Path, name: str) -> tuple[Path, str]:
    target = _target(tmp_path, name)
    project = f"m127-test-{uuid.uuid4().hex[:12]}"
    compose = _with_measurement_apparmor(
        (REPO_ROOT / "deploy/compose.worker.standalone.yaml").read_text(encoding="utf-8")
    )
    (target / "docker-compose.yaml").write_text(
        compose.replace("name: agent-legion-worker", f"name: {project}", 1), encoding="utf-8"
    )
    (target / ".env").write_text("AGENT_WORKER_UI_PORT=0\n", encoding="utf-8")
    (target / "velites-bin/velites").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = models ]; then\n'
        "  exec python3 -c 'import json,os; "
        'data=json.load(open(os.path.expanduser("~/.velites/models.json"), encoding="utf-8")); '
        'print(json.dumps(data["models"]))\'\n'
        "fi\n"
        'if [ "$1" = --version ]; then echo velites-nonroot-test; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    (target / "velites-bin/velites").chmod(0o755)
    (target / "velites-config").mkdir(exist_ok=True)
    (target / "velites-config/models.json").write_text(
        json.dumps({"models": [{"provider": "stub", "model": "nonroot-ok"}]}), encoding="utf-8"
    )
    (target / "velites-config/models.json").chmod(0o600)
    return target, project


def _run_real_nonroot_installer(tmp_path: Path, target: Path) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "real-nonroot-bin"
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
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (fake_bin / "curl").chmod(0o755)
    env = os.environ.copy()
    for key in ("AGENT_WORKER_UID", "AGENT_WORKER_GID"):
        env.pop(key, None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_COMPOSE": str(target / "docker-compose.yaml"),
            "FAKE_MIGRATION": str(REPO_ROOT / "scripts/migrate-worker-volumes.sh"),
            "AGENT_WORKER_VERSION": "0.8.2",
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


def _compose(target: Path, *args: str, compose="docker-compose.yaml", check=True):
    return subprocess.run(
        ["docker", "compose", "-f", compose, *args],
        cwd=target,
        capture_output=True,
        text=True,
        check=check,
    )


def _wait_worker_api(target: Path, compose: str = "docker-compose.yaml") -> None:
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


def _volume_python(project: str, image: str, script: str, *args: str) -> None:
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
            image,
            "-c",
            script,
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _seed_root_owned_volumes(project: str, image: str) -> None:
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
(control / "register_tokens").mkdir(parents=True, exist_ok=True)
(control / "logs").mkdir(exist_ok=True)
(control / "evidence").mkdir(exist_ok=True)
(data / "exec-pending").mkdir(parents=True, exist_ok=True)
config = {{"host_url": "http://127.0.0.1:9", "worker_id": "root-owned", "work_root": "/var/lib/agent-legion-worker", "register_token_dir": "/var/lib/agent-legion-worker-control/register_tokens"}}
(control / "worker.yaml").write_text(json.dumps(config), encoding="utf-8")
(control / "control_token").write_text("root-owned-control-token\\n", encoding="utf-8")
(control / "register_tokens/stub.token").write_text("stub.root-owned-secret\\n", encoding="utf-8")
(control / "logs/bootstrap.log").write_text("root-owned-log\\n", encoding="utf-8")
(control / "evidence/prep.json").write_text('{{"root_owned": true}}', encoding="utf-8")
(data / "exec-pending/upload_pending.json").write_text(json.dumps({marker!r}), encoding="utf-8")
(data / "exec-pending/events.jsonl").write_text('{{"event":"root-owned"}}\\n', encoding="utf-8")
(data / "exec-pending/metrics.json").write_text('{{"rss":1}}', encoding="utf-8")
(data / "exec-pending/tombstone.json").write_text('{{"delivered":false}}', encoding="utf-8")
for root in (control, data):
    for directory, dirs, files in os.walk(root):
        for name in [*dirs, *files]:
            os.chmod(Path(directory) / name, 0o600 if name in files else 0o700)
"""
    _volume_python(project, image, script)


def _assert_volume_tree_owned(project: str, image: str, uid: int, gid: int) -> None:
    script = """import os,sys
uid,gid = (int(value) for value in sys.argv[1:])
for root in ("/control", "/data"):
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(directory, name)
            stat = os.lstat(path)
            assert (stat.st_uid, stat.st_gid) == (uid, gid), (path, stat.st_uid, stat.st_gid)
        for name in files:
            if name == "worker.yaml" or name == "control_token" or name.endswith(".token"):
                mode = os.lstat(os.path.join(directory, name)).st_mode & 0o777
                assert mode == 0o600, (directory, name, oct(mode))
"""
    _volume_python(project, image, script, str(uid), str(gid))


@pytest.mark.parametrize("root_owned", [False, True], ids=["fresh", "root-owned"])
@pytest.mark.skipif(
    os.environ.get("AGENT_LEGION_DOCKER_TESTS") != "1",
    reason="真实 Docker 迁移验收需 AGENT_LEGION_DOCKER_TESTS=1",
)
def test_installer_real_nonroot_migration_service_and_rollback(
    tmp_path: Path, nonroot_worker_image: str, root_owned: bool
) -> None:
    target, project = _real_nonroot_project(tmp_path, f"real-{root_owned}")
    if root_owned:
        _seed_root_owned_volumes(project, nonroot_worker_image)
    old_compose = "docker-compose.root-rollback.yaml"
    try:
        result = _run_real_nonroot_installer(tmp_path, target)
        assert result.returncode == 0, result.stderr
        secrets = (target / "worker.yaml", target / "velites-config/models.json")
        assert {path.stat().st_mode & 0o777 for path in secrets} == {0o600}
        assert {path.stat().st_uid for path in secrets} == {os.getuid()}
        _compose(target, "up", "-d")
        _wait_worker_api(target)
        identity = _compose(
            target,
            "exec",
            "-T",
            "worker",
            "sh",
            "-c",
            "id -u; id -g; printf %s $HOME; test ! -u /usr/bin/bwrap; bwrap --unshare-all --die-with-parent --ro-bind / / -- /bin/true",
        )
        assert identity.stdout.splitlines() == [
            str(os.getuid()),
            str(os.getgid()),
            "/home/agent-worker",
        ]
        status = _compose(target, "exec", "-T", "worker", "workerctl", "status")
        assert "服务: running" in status.stdout
        _compose(
            target,
            "exec",
            "-T",
            "worker",
            "python3",
            "-c",
            "from worker.runtime.models import discover_effective_models; "
            "effective, errors = discover_effective_models({'runtimes': ['velites'], 'models': []}); "
            "assert not errors, errors; "
            "assert effective == [{'runtime': 'velites', 'provider': 'stub', 'model': 'nonroot-ok'}], effective",
        )
        _assert_volume_tree_owned(project, nonroot_worker_image, os.getuid(), os.getgid())
        if root_owned:
            token = _compose(
                target,
                "exec",
                "-T",
                "worker",
                "cat",
                "/var/lib/agent-legion-worker-control/control_token",
            )
            assert token.stdout.strip() == "root-owned-control-token"
            preserved = _compose(
                target,
                "exec",
                "-T",
                "worker",
                "sh",
                "-c",
                "cat /var/lib/agent-legion-worker-control/register_tokens/stub.token; "
                "cat /var/lib/agent-legion-worker-control/logs/bootstrap.log; "
                "cat /var/lib/agent-legion-worker/exec-pending/events.jsonl; "
                "test -f /var/lib/agent-legion-worker/exec-pending/upload_pending.json; "
                "test -f /var/lib/agent-legion-worker/exec-pending/metrics.json; "
                "test -f /var/lib/agent-legion-worker/exec-pending/tombstone.json",
            )
            assert "stub.root-owned-secret" in preserved.stdout
            assert "root-owned-log" in preserved.stdout and "root-owned" in preserved.stdout
        _compose(target, "restart", "worker")
        _wait_worker_api(target)
        _compose(target, "exec", "-T", "worker", "workerctl", "status")
        if root_owned:
            _compose(
                target,
                "exec",
                "-T",
                "worker",
                "test",
                "-f",
                "/var/lib/agent-legion-worker/exec-pending/upload_pending.json",
            )
            root_image = f"{nonroot_worker_image}-root-rollback"
            subprocess.run(
                ["docker", "build", "-t", root_image, "-f", "-", str(tmp_path)],
                input=f"FROM {nonroot_worker_image}\nUSER 0:0\n",
                text=True,
                check=True,
                capture_output=True,
            )
            compose = (target / "docker-compose.yaml").read_text(encoding="utf-8")
            compose = compose.replace(
                "    user: ${AGENT_WORKER_UID:-10001}:${AGENT_WORKER_GID:-10001}\n", ""
            )
            compose = compose.replace(
                "image: ${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.6.0}",
                f"image: {root_image}",
            ).replace("/home/agent-worker", "/root")
            (target / old_compose).write_text(compose, encoding="utf-8")
            _compose(target, "up", "-d", compose=old_compose)
            _wait_worker_api(target, old_compose)
            rollback = _compose(
                target,
                "exec",
                "-T",
                "worker",
                "sh",
                "-c",
                "id -u; workerctl status",
                compose=old_compose,
            )
            assert rollback.stdout.splitlines()[0] == "0"
            assert "服务: running" in rollback.stdout
    finally:
        subprocess.run(
            ["docker", "compose", "-f", old_compose, "down"], cwd=target, capture_output=True
        )
        subprocess.run(
            ["docker", "compose", "-f", "docker-compose.yaml", "down"],
            cwd=target,
            capture_output=True,
        )
        subprocess.run(
            ["docker", "volume", "rm", "-f", f"{project}_worker-control", f"{project}_worker-data"],
            capture_output=True,
        )
