"""install-worker.sh 的发布契约守卫（codex P1 on #583 的防复发）。

两类复发事故：
1. sha256 解析漂移（#583）：发布端 velites-release.yml 用
   ``sha256sum ./*.tar.gz`` 生成校验清单，文件名带 ``./`` 前缀；安装端
   awk 精确匹配裸文件名，恒为空 → 默认升级路径中止。本测试把脚本里
   真实的 awk 管道抠出来，对「发布端同款格式」的合成清单执行，
   钉住解析契约。
2. 默认版本漂移（#529 的同类）：install-worker.sh 的 VELITES_VERSION
   默认值必须与 velites/Cargo.toml 的 package version 一致——velites
   落版 bump 是两处手工同步点，漏一边就是「安装默认装旧版」。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "install-worker.sh"
CARGO_TOML = REPO_ROOT / "velites" / "Cargo.toml"

TARBALL = "velites-0.0.0-test-aarch64-apple-darwin.tar.gz"
HASH = "a" * 64


def _extract_expected_pipeline(script: str) -> str:
    """抠出脚本里 expected=$(...) 的完整命令替换体。"""
    match = re.search(r'expected="\$\((awk .+?)\)"', script)
    assert match, "install-worker.sh 里找不到 expected 的 awk 管道（结构变了就同步本测试）"
    return match.group(1)


def test_sha256_listing_parses_release_side_dot_slash_format(tmp_path: Path) -> None:
    # 发布端格式（velites-release.yml 的 sha256sum 输出）：文件名带 ./ 前缀
    (tmp_path / "sha256.txt").write_text(
        f"{HASH}  ./{TARBALL}\n{'b' * 64}  ./velites-0.0.0-test-x86_64-unknown-linux-gnu.tar.gz\n"
    )
    pipeline = _extract_expected_pipeline(INSTALLER.read_text())
    result = subprocess.run(
        ["bash", "-c", f"{pipeline}"],
        env={"VELITES_TARBALL": TARBALL, "TMPDIR_": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == HASH


def test_sha256_listing_parses_bare_filename_format(tmp_path: Path) -> None:
    # 无前缀格式（velites-release 已归一为裸 glob）同样必须命中
    (tmp_path / "sha256.txt").write_text(f"{HASH}  {TARBALL}\n")
    pipeline = _extract_expected_pipeline(INSTALLER.read_text())
    result = subprocess.run(
        ["bash", "-c", f"{pipeline}"],
        env={"VELITES_TARBALL": TARBALL, "TMPDIR_": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == HASH


def test_installer_default_version_matches_velites_crate() -> None:
    script = INSTALLER.read_text()
    default = re.search(r'VELITES_VERSION="\$\{VELITES_VERSION:-([^}]+)\}"', script)
    assert default, "install-worker.sh 里找不到 VELITES_VERSION 默认值"
    package = re.search(r'(?ms)^\[package\].*?^version = "([^"]+)"', CARGO_TOML.read_text())
    assert package, "velites/Cargo.toml 里找不到 package version"
    assert default.group(1) == package.group(1), (
        f"install-worker.sh 默认 velites 版本 {default.group(1)} 与 "
        f"velites/Cargo.toml {package.group(1)} 不一致——velites 落版时两处必须同步 bump"
    )


def _target(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    (target / "velites-bin").mkdir(parents=True)
    (target / "velites-bin/velites").write_text("binary", encoding="utf-8")
    (target / "velites-bin/.velites-version").write_text("0.5.9", encoding="utf-8")
    return target


def _run_installer(
    tmp_path: Path, target: Path, *, version: str = "0.8.2", extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "docker").write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"\n'
        'if [ "$1" = run ]; then\n'
        '  case " $* " in *" -i "*) printf "%s\\n" "agent-legion-worker_worker-control agent-legion-worker_worker-data"; exit 0 ;; esac\n'
        '  case " $* " in *":/var/lib/agent-legion-worker "*) [ "${FAKE_MIGRATION_FAIL:-0}" = 0 ]; exit $? ;; esac\n'
        '  [ "${FAKE_DOCKER_RUN_FAIL:-0}" = 0 ] || exit 1\n'
        '  "$FAKE_PYTHON" -c \'import os,yaml\n'
        "from pathlib import Path\n"
        "from worker.config_validation import validate_config\n"
        'with open(os.environ["FAKE_CONTROL_YAML"] if os.environ.get("FAKE_CONTROL_VOLUME_PRESENT") == "1" else os.environ["FAKE_WORKER_YAML"], encoding="utf-8") as stream:\n'
        "    config = validate_config(yaml.safe_load(stream), require_identity=False)\n"
        'roots = (Path("/var/lib/agent-legion-worker"), Path("/var/lib/agent-legion-worker-control"))\n'
        "def covered(value):\n"
        "    path = Path(str(value))\n"
        '    return path.is_absolute() and ".." not in path.parts and any(path == root or root in path.parents for root in roots)\n'
        'assert covered(config.get("work_root", "/var/lib/agent-legion-worker")), "unsafe work_root"\n'
        'assert not config.get("register_token_file") or covered(config["register_token_file"]), "unsafe register_token_file"\n'
        'assert not config.get("register_token_dir") or covered(config["register_token_dir"]), "unsafe register_token_dir"\n'
        'print(config["max_concurrency"], config["max_code_concurrency"], config["upload_max_concurrency"])\'\n'
        "  exit $?\n"
        "fi\n"
        'if [ "$1" = volume ]; then\n'
        '  [ "${FAKE_CONTROL_VOLUME_PRESENT:-0}" = 1 ] && printf "%s\\n" agent-legion-worker_worker-control\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = compose ] && [ "${4:-}" = stop ]; then\n'
        '  [ "${FAKE_STOP_FAIL:-0}" = 0 ]; exit $?\n'
        "fi\n"
        'if [ "$1" = compose ] && [ "${4:-}" = ps ]; then\n'
        '  printf "%s\\n" "${FAKE_RUNNING_WORKER:-}"; exit 0\n'
        "fi\n"
        'if [ "$1" = compose ] && [ "${5:-}" = --format ] && [ "${FAKE_COMPOSE_CONFIG_EXIT:-0}" != 0 ]; then\n'
        '  printf "%s\\n" \'{"volumes":{"worker-control":{"name":"agent-legion-worker_worker-control"}}}\'\n'
        '  exit "$FAKE_COMPOSE_CONFIG_EXIT"\n'
        "fi\n"
        'if [ "$1" = compose ] && [ "$2" = version ]; then\n'
        '  [ "${3:-}" = --short ] && echo v2.29.0 || echo Docker Compose version v2.29.0\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
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
    for command in ("docker", "curl"):
        (fake_bin / command).chmod(0o755)
    env = os.environ.copy()
    for key in (
        "AGENT_WORKER_RESOURCE_LIMITS_MODE",
        "AGENT_WORKER_MEM_LIMIT",
        "AGENT_WORKER_PIDS_LIMIT",
        "AGENT_WORKER_UID",
        "AGENT_WORKER_GID",
    ):
        env.pop(key, None)
    env.update(extra_env or {})
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
            "FAKE_COMPOSE": str(REPO_ROOT / "deploy/compose.worker.standalone.yaml"),
            "FAKE_WORKER_YAML": str(target / "worker.yaml"),
            "FAKE_CONTROL_YAML": str(target / "control-worker.yaml"),
            "FAKE_PYTHON": sys.executable,
            "PYTHONPATH": str(REPO_ROOT),
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


def _env(target: Path) -> dict[str, str]:
    return {
        key: value
        for line in (target / ".env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, value in [line.split("=", 1)]
    }


def test_installer_fresh_limits_are_persistent_and_idempotent(tmp_path: Path) -> None:
    target = _target(tmp_path, "fresh")
    result = _run_installer(tmp_path, target)
    assert result.returncode == 0, result.stderr
    expected = {
        "AGENT_WORKER_RESOURCE_LIMITS_MODE": "default",
        "AGENT_WORKER_MEM_LIMIT": "256m",
        "AGENT_WORKER_PIDS_LIMIT": "128",
    }
    assert _env(target) | expected == _env(target)
    before = (target / ".env").read_bytes()
    result = _run_installer(tmp_path, target)
    assert result.returncode == 0, result.stderr
    assert (target / ".env").read_bytes() == before


def test_installer_upgrade_rollback_preserves_explicit_limits_and_upload_state(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path, "upgrade")
    (target / "worker.yaml").write_text("max_concurrency: 11\n", encoding="utf-8")
    (target / ".env").write_text(
        "AGENT_WORKER_RESOURCE_LIMITS_MODE=explicit\n"
        "AGENT_WORKER_MEM_LIMIT=512m\n"
        "AGENT_WORKER_PIDS_LIMIT=128\n",
        encoding="utf-8",
    )
    for version in ("0.8.3", "0.8.2"):
        result = _run_installer(tmp_path, target, version=version)
        assert result.returncode == 0, result.stderr
        assert _env(target)["AGENT_WORKER_MEM_LIMIT"] == "512m"
        assert _env(target)["AGENT_WORKER_PIDS_LIMIT"] == "128"
        assert _env(target)["AGENT_WORKER_RESOURCE_LIMITS_MODE"] == "explicit"
        assert _env(target)["AGENT_WORKER_IMAGE"].endswith(f":{version}")
    compose = (target / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "worker-data:/var/lib/agent-legion-worker" in compose
    assert (target / "worker.yaml").read_text(encoding="utf-8") == "max_concurrency: 11\n"


def test_installer_default_upgrade_rejects_high_concurrency_before_commit(tmp_path: Path) -> None:
    target = _target(tmp_path, "reject")
    (target / "worker.yaml").write_text("max_concurrency: 11\n", encoding="utf-8")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    (target / "docker-compose.yaml").write_text("old-compose\n", encoding="utf-8")
    result = _run_installer(tmp_path, target)
    assert result.returncode == 1
    assert "测量 envelope" in result.stderr and "explicit" in result.stderr
    assert (target / ".env").read_text(encoding="utf-8") == "AGENT_WORKER_IMAGE=old\n"
    assert (target / "docker-compose.yaml").read_text(encoding="utf-8") == "old-compose\n"


@pytest.mark.parametrize(
    ("document", "error"),
    [
        ("  max_concurrency: 0xB\n", "测量 envelope"),
        ("max_code_concurrency: 0xB\n", "测量 envelope"),
        ("upload_max_concurrency: 0xB\n", "测量 envelope"),
        ("max_concurrency: 0xB # eleven\n", "测量 envelope"),
        ("max_concurrency: 10\nmax_concurrency: 0xB\n", "测量 envelope"),
        ("max_concurrency: '0xB'\n", "可靠解析"),
        ('max_concurrency: "11"\n', "可靠解析"),
        ("max_concurrency: true\n", "可靠解析"),
        ("max_concurrency: eleven\n", "可靠解析"),
        ("max_concurrency: [11\n", "可靠解析"),
        ("- max_concurrency\n", "可靠解析"),
        ("max_concurrency: 0xB\nmax_concurrency: 10\n", None),
    ],
)
def test_installer_uses_worker_yaml_semantics_before_commit(
    tmp_path: Path, document: str, error: str | None
) -> None:
    target = _target(tmp_path, "yaml-semantics")
    (target / "worker.yaml").write_text(document, encoding="utf-8")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    (target / "docker-compose.yaml").write_text("old-compose\n", encoding="utf-8")
    result = _run_installer(tmp_path, target)
    if error is None:
        assert result.returncode == 0, result.stderr
        assert _env(target)["AGENT_WORKER_RESOURCE_LIMITS_MODE"] == "default"
        return
    assert result.returncode == 1
    assert error in result.stderr
    assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"
    assert (target / "docker-compose.yaml").read_bytes() == b"old-compose\n"


def test_installer_unavailable_yaml_parser_fails_closed_before_commit(tmp_path: Path) -> None:
    target = _target(tmp_path, "parser-unavailable")
    (target / "worker.yaml").write_text("max_concurrency: 0xB\n", encoding="utf-8")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    (target / "docker-compose.yaml").write_text("old-compose\n", encoding="utf-8")
    result = _run_installer(tmp_path, target, extra_env={"FAKE_DOCKER_RUN_FAIL": "1"})
    assert result.returncode == 1 and "可靠解析" in result.stderr
    assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"
    assert (target / "docker-compose.yaml").read_bytes() == b"old-compose\n"


def test_installer_compose_config_nonzero_with_valid_json_fails_before_commit(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path, "compose-config-failure")
    (target / "worker.yaml").write_text("max_concurrency: 10\n", encoding="utf-8")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    (target / "docker-compose.yaml").write_text("old-compose\n", encoding="utf-8")
    result = _run_installer(tmp_path, target, extra_env={"FAKE_COMPOSE_CONFIG_EXIT": "7"})
    assert result.returncode == 1 and "Compose config 执行失败" in result.stderr
    assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"
    assert (target / "docker-compose.yaml").read_bytes() == b"old-compose\n"


def test_installer_persists_explicit_limits_and_unlimited(tmp_path: Path) -> None:
    for name, mem, pids in (("finite", "512m", "128"), ("unlimited", "0", "-1")):
        target = _target(tmp_path, name)
        (target / "worker.yaml").write_text("max_concurrency: 11\n", encoding="utf-8")
        result = _run_installer(
            tmp_path,
            target,
            extra_env={"AGENT_WORKER_MEM_LIMIT": mem, "AGENT_WORKER_PIDS_LIMIT": pids},
        )
        assert result.returncode == 0, result.stderr
        assert _env(target)["AGENT_WORKER_RESOURCE_LIMITS_MODE"] == "explicit"
        assert _env(target)["AGENT_WORKER_MEM_LIMIT"] == mem
        assert _env(target)["AGENT_WORKER_PIDS_LIMIT"] == pids


@pytest.mark.parametrize("document", ["max_concurrency: 11\n", "max_code_concurrency: 1\n"])
def test_installer_mock_control_volume_high_concurrency_fails_before_commit(
    tmp_path: Path, document: str
) -> None:
    target = _target(tmp_path, "mock-control")
    (target / "worker.yaml").write_text("max_concurrency: 10\n", encoding="utf-8")
    (target / "control-worker.yaml").write_text(document, encoding="utf-8")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    (target / "docker-compose.yaml").write_text("old-compose\n", encoding="utf-8")
    result = _run_installer(tmp_path, target, extra_env={"FAKE_CONTROL_VOLUME_PRESENT": "1"})
    assert result.returncode == 1 and "测量 envelope" in result.stderr
    assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"
    assert (target / "docker-compose.yaml").read_bytes() == b"old-compose\n"


def test_installer_mock_control_volume_explicit_upgrade_and_invalid_yaml(tmp_path: Path) -> None:
    target = _target(tmp_path, "mock-explicit")
    (target / "worker.yaml").write_text("max_concurrency: 10\n", encoding="utf-8")
    (target / "control-worker.yaml").write_text("max_concurrency: 11\n", encoding="utf-8")
    (target / ".env").write_text(
        "AGENT_WORKER_RESOURCE_LIMITS_MODE=explicit\n"
        "AGENT_WORKER_MEM_LIMIT=512m\n"
        "AGENT_WORKER_PIDS_LIMIT=256\n",
        encoding="utf-8",
    )
    result = _run_installer(tmp_path, target, extra_env={"FAKE_CONTROL_VOLUME_PRESENT": "1"})
    assert result.returncode == 0, result.stderr
    assert _env(target)["AGENT_WORKER_MEM_LIMIT"] == "512m"
    assert _env(target)["AGENT_WORKER_PIDS_LIMIT"] == "256"
    (target / "control-worker.yaml").write_text("max_concurrency: [11\n", encoding="utf-8")
    before_env = (target / ".env").read_bytes()
    before_compose = (target / "docker-compose.yaml").read_bytes()
    result = _run_installer(tmp_path, target, extra_env={"FAKE_CONTROL_VOLUME_PRESENT": "1"})
    assert result.returncode == 1 and "可靠解析" in result.stderr
    assert (target / ".env").read_bytes() == before_env
    assert (target / "docker-compose.yaml").read_bytes() == before_compose


def test_installer_nonroot_fresh_persists_identity_and_migrates_only_named_volumes(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path, "nonroot-fresh")
    result = _run_installer(tmp_path, target)
    assert result.returncode == 0, result.stderr
    assert _env(target)["AGENT_WORKER_UID"] == str(os.getuid())
    assert _env(target)["AGENT_WORKER_GID"] == str(os.getgid())
    assert (target / "worker.yaml").stat().st_mode & 0o777 == 0o600
    assert (target / "velites-bin/velites").stat().st_mode & 0o777 == 0o755
    calls = (tmp_path / "docker.log").read_text(encoding="utf-8").splitlines()
    migration = next(
        line
        for line in calls
        if line.startswith("run --rm") and ":/var/lib/agent-legion-worker " in line
    )
    assert "--network none --user 0:0" in migration
    assert "agent-legion-worker_worker-control:/var/lib/agent-legion-worker-control" in migration
    assert "agent-legion-worker_worker-data:/var/lib/agent-legion-worker" in migration
    assert str(target) not in migration and "$HOME" not in migration
    stop_index = next(i for i, line in enumerate(calls) if " stop worker" in line)
    ps_index = next(i for i, line in enumerate(calls) if " ps --status running -q worker" in line)
    migration_index = calls.index(migration)
    assert stop_index < ps_index < migration_index


@pytest.mark.parametrize("uid", ["0", "01001", "not-a-number"])
def test_installer_rejects_root_or_invalid_runtime_uid_before_commit(
    tmp_path: Path, uid: str
) -> None:
    target = _target(tmp_path, "bad-uid")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    result = _run_installer(tmp_path, target, extra_env={"AGENT_WORKER_UID": uid})
    assert result.returncode == 1 and "AGENT_WORKER_UID" in result.stderr
    assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"


@pytest.mark.parametrize(
    "path_line",
    [
        "work_root: /srv/worker-data\n",
        "register_token_file: /etc/register.token\n",
        "register_token_dir: /srv/register-tokens\n",
    ],
)
def test_installer_rejects_custom_paths_outside_volume_migration(
    tmp_path: Path, path_line: str
) -> None:
    target = _target(tmp_path, "unsafe-path")
    (target / "worker.yaml").write_text(f"max_concurrency: 10\n{path_line}", encoding="utf-8")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    (target / "docker-compose.yaml").write_text("old-compose\n", encoding="utf-8")
    result = _run_installer(tmp_path, target)
    assert result.returncode == 1 and "named volumes" in result.stderr
    assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"
    assert (target / "docker-compose.yaml").read_bytes() == b"old-compose\n"
    calls = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert ":/var/lib/agent-legion-worker " not in calls


def test_installer_allows_custom_paths_covered_by_recursive_volume_migration(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path, "covered-paths")
    (target / "worker.yaml").write_text(
        "work_root: /var/lib/agent-legion-worker/nested\n"
        "register_token_dir: /var/lib/agent-legion-worker-control/tokens\n",
        encoding="utf-8",
    )
    result = _run_installer(tmp_path, target)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("failure_env", "message"),
    [
        ({"FAKE_STOP_FAIL": "1"}, "停止 Worker 失败"),
        ({"FAKE_RUNNING_WORKER": "container-still-running"}, "仍在运行"),
        ({"FAKE_MIGRATION_FAIL": "1"}, "属主迁移失败"),
    ],
)
def test_installer_migration_failures_do_not_commit_compose_or_env(
    tmp_path: Path, failure_env: dict[str, str], message: str
) -> None:
    target = _target(tmp_path, "migration-failure")
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    (target / "docker-compose.yaml").write_text("old-compose\n", encoding="utf-8")
    result = _run_installer(tmp_path, target, extra_env=failure_env)
    assert result.returncode == 1 and message in result.stderr
    assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"
    assert (target / "docker-compose.yaml").read_bytes() == b"old-compose\n"


def test_installer_rejects_group_writable_mount_parent_before_commit(tmp_path: Path) -> None:
    target = _target(tmp_path, "unsafe-parent")
    target.chmod(0o777)
    (target / ".env").write_text("AGENT_WORKER_IMAGE=old\n", encoding="utf-8")
    result = _run_installer(tmp_path, target)
    assert result.returncode == 1 and "group/other" in result.stderr
    assert (target / ".env").read_bytes() == b"AGENT_WORKER_IMAGE=old\n"


def test_installer_rejects_symlink_bootstrap_without_touching_target(tmp_path: Path) -> None:
    target = _target(tmp_path, "symlink-bootstrap")
    outside = tmp_path / "outside.yaml"
    outside.write_text("max_concurrency: 10\n", encoding="utf-8")
    (target / "worker.yaml").symlink_to(outside)
    result = _run_installer(tmp_path, target)
    assert result.returncode == 1 and "symlink" in result.stderr
    assert outside.stat().st_mode & 0o777 != 0o600


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


def _real_nonroot_project(tmp_path: Path, name: str) -> tuple[Path, str]:
    target = _target(tmp_path, name)
    project = f"m127-test-{uuid.uuid4().hex[:12]}"
    compose = (REPO_ROOT / "deploy/compose.worker.standalone.yaml").read_text(encoding="utf-8")
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


def _compose(
    target: Path, *args: str, compose: str = "docker-compose.yaml", check: bool = True
) -> subprocess.CompletedProcess[str]:
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
        assert (target / "worker.yaml").stat().st_mode & 0o777 == 0o600
        assert (target / "velites-config/models.json").stat().st_mode & 0o777 == 0o600
        assert (target / "worker.yaml").stat().st_uid == os.getuid()
        assert (target / "velites-config/models.json").stat().st_uid == os.getuid()
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
        models = _compose(
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
        assert models.returncode == 0
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
