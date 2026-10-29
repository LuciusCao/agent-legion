from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_worker_image_build_reuses_existing_gid_20() -> None:
    project = f"m127-test-{uuid.uuid4().hex[:12]}"
    image = f"agent-legion-worker:{project}"
    control = f"{project}-control"
    data = f"{project}-data"
    try:
        build = subprocess.run(
            [
                "docker",
                "build",
                "--target",
                "worker",
                "--build-arg",
                "WORKER_UID=501",
                "--build-arg",
                "WORKER_GID=20",
                "-t",
                image,
                ".",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        script = """set -eu
test "$(id -u):$(id -g)" = "501:20"
test "$HOME" = "/home/agent-worker"
getent passwd 501 >/dev/null
getent group 20 >/dev/null
control=/var/lib/agent-legion-worker-control
data=/var/lib/agent-legion-worker
for path in /home/agent-worker "$control" "$data" "$control/.volume-keep" "$data/.volume-keep"; do
    test "$(stat -c '%u:%g' "$path")" = "501:20"
done
umask 077
printf 'worker_id: image-identity\n' > "$control/worker.yaml"
test "$(stat -c '%u:%g:%a' "$control/worker.yaml")" = "501:20:600"
"""
        run = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{control}:/var/lib/agent-legion-worker-control",
                "-v",
                f"{data}:/var/lib/agent-legion-worker",
                "--entrypoint",
                "sh",
                image,
                "-c",
                script,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 0, run.stdout + run.stderr
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", control, data], capture_output=True)
        subprocess.run(["docker", "image", "rm", "-f", image], capture_output=True)
