"""Static fail-closed contracts for the native Ubuntu spike (no host changes)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.e2e.native_worker_spike_payload import REQUIRED_CHECKS, _stdio_only, check_failures

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "scripts" / "e2e" / "native_worker_spike.service"
SCRIPT = ROOT / "scripts" / "e2e" / "native_worker_spike.sh"
HARNESS = ROOT / "scripts" / "e2e" / "native_worker_spike.py"
AGENT = ROOT / "scripts" / "e2e" / "native_worker_spike_agent.py"
WORKFLOW = ROOT / ".github" / "workflows" / "worker-native-spike.yml"


def _unit_settings() -> dict[str, str]:
    settings = {}
    in_service = False
    for raw in UNIT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "[Service]":
            in_service = True
        elif line.startswith("["):
            in_service = False
        elif in_service and line and not line.startswith("#"):
            key, value = line.split("=", 1)
            settings[key] = value
    return settings


def test_n0_systemd_identity_and_hardening() -> None:
    settings = _unit_settings()
    assert settings["Type"] == "oneshot"
    assert settings["User"] == "agent-legion-spike"
    assert settings["Group"] == "agent-legion-spike"
    unit = UNIT.read_text(encoding="utf-8")
    assert "Environment=HOME=/var/lib/agent-legion-native-spike\n" in unit
    assert "StateDirectory=agent-legion-native-spike\n" in unit
    assert settings["NoNewPrivileges"] == "yes"
    assert settings["CapabilityBoundingSet"] == ""
    assert settings["AmbientCapabilities"] == ""
    assert settings["SecureBits"].startswith("noroot noroot-locked")
    assert settings["ProtectSystem"] == "strict"
    assert settings["ReadWritePaths"] == "/var/lib/agent-legion-native-spike"
    assert settings["RestrictSUIDSGID"] == "yes"
    assert settings["KillMode"] == "mixed"
    assert settings["TimeoutStopSec"] == "35s"
    assert "native_worker_spike.py" in settings["ExecStart"]


def test_no_privilege_or_docker_worker_escape_hatches() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (UNIT, SCRIPT, WORKFLOW, HARNESS)
    ).lower()
    for forbidden in (
        "sys_admin",
        "apparmor",
        "sysctl",
        "strace",
        "ld_preload",
        "--no-sandbox",
        "docker run",
        "privileged: true",
        "chmod u+s",
        "setcap",
        "userns-remap",
    ):
        assert forbidden not in source
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["native-spike"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert set(job["services"]) == {"postgres"}
    assert "image" not in job


def test_mechanism_contracts_use_dedicated_service_test_database() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["native-spike"]
    postgres = job["services"]["postgres"]
    step = next(
        step for step in job["steps"] if step.get("name") == "Run native-spike mechanism contracts"
    )
    user = postgres["env"].get("POSTGRES_USER", "postgres")
    password = postgres["env"]["POSTGRES_PASSWORD"]
    host_port = str(postgres["ports"][0]).split(":", 1)[0]
    expected = (
        f"postgresql://{user}:{password}@127.0.0.1:{host_port}/agent_legion_native_spike_test"
    )
    assert step["env"]["AGENT_LEGION_TEST_DATABASE_URL"] == expected
    assert "tests/e2e/native_worker_spike_resume_test.py" in step["run"]


def test_ci_systemd_install_is_explicitly_guarded() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "CI:-} != true" in source
    assert "NATIVE_SPIKE_ALLOW_SYSTEMD_INSTALL:-} != 1" in source
    assert "systemctl start --wait" in source
    assert "systemd-runtime.txt" in source
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "sudo --preserve-env=CI,NATIVE_SPIKE_ALLOW_SYSTEMD_INSTALL" in workflow
    assert "systemd-analyze verify" in workflow
    assert "actions/upload-artifact@" in workflow


def test_systemd_verify_and_wait_follow_the_installed_service_lifecycle() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = {step.get("name"): step for step in workflow["jobs"]["native-spike"]["steps"]}
    names = list(steps)
    assert names.index("Verify shell") < names.index("Install the one-shot N0 CI service")
    assert (
        names.index("Install the one-shot N0 CI service")
        < names.index("Verify installed systemd unit")
        < names.index("Run direct, Host, real-claim Worker and agent probes under N0")
    )
    assert "bash -n" in steps["Verify shell"]["run"]
    assert steps["Verify installed systemd unit"]["run"] == (
        "systemd-analyze verify /etc/systemd/system/agent-legion-native-spike.service"
    )
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'systemctl start --wait "$UNIT_NAME" &' in source
    assert source.count("systemctl start ") == 1
    assert "systemctl wait " not in source
    assert (
        source.index("main_pid=$(systemctl show")
        < source.index('wait "$start_wait_pid"')
        < source.index("--property=Result")
    )


def test_every_payload_boundary_is_mandatory_and_mutation_detected() -> None:
    document = {"checks": {name: True for name in REQUIRED_CHECKS}}
    assert check_failures(document) == []
    for name in REQUIRED_CHECKS:
        document["checks"][name] = False
        assert check_failures(document) == [name]
        document["checks"][name] = True
    document["checks"] = json.loads(json.dumps(document["checks"]))
    del document["checks"]["private_proc"]
    assert "private_proc" in check_failures(document)


@pytest.mark.parametrize(
    ("directory_fd", "extra_fd", "expected"),
    [(3, None, True), (4, 3, False)],
    ids=["enumeration-only", "inherited-extra-fd"],
)
def test_stdio_only_excludes_only_current_enumeration(
    monkeypatch, directory_fd, extra_fd, expected
) -> None:
    directory_stat = SimpleNamespace(st_dev=2, st_ino=2)
    other_stat = SimpleNamespace(st_dev=1, st_ino=1)
    stats = {0: other_stat, 1: other_stat, 2: other_stat, directory_fd: directory_stat}
    if extra_fd is not None:
        stats[extra_fd] = other_stat
    entries = [
        SimpleNamespace(name=str(fd), stat=lambda *, follow_symlinks, stat=stat: stat)
        for fd, stat in sorted(stats.items())
    ]
    closed = []

    @contextmanager
    def fake_scandir(_path):
        yield iter(entries)
        closed.append(True)

    real_stat = os.stat

    def fake_stat(path, **kwargs):
        if str(path) == "/proc/self/fd":
            return directory_stat
        return real_stat(path, **kwargs)

    monkeypatch.setattr(os, "stat", fake_stat)
    monkeypatch.setattr(os, "scandir", fake_scandir)
    monkeypatch.setattr(os, "listdir", lambda _path: [entry.name for entry in entries])
    assert _stdio_only() is expected
    assert closed == [True]


def _linux_fd_child(*, pass_fds=()) -> subprocess.CompletedProcess:
    code = (
        "from scripts.e2e.native_worker_spike_payload import _stdio_only; "
        "raise SystemExit(0 if _stdio_only() else 1)"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        pass_fds=pass_fds,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux procfs")
def test_linux_clean_child_has_stdio_only() -> None:
    completed = _linux_fd_child()
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux procfs")
def test_linux_inherited_extra_fd_fails_closed() -> None:
    extra_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        completed = _linux_fd_child(pass_fds=(extra_fd,))
    finally:
        os.close(extra_fd)
    assert completed.returncode == 1, completed.stderr


def test_worker_path_uses_real_dispatch_claim_and_process_entrypoints() -> None:
    source = HARNESS.read_text(encoding="utf-8")
    assert (
        source.index('_wait_http(f"{host_url}/api/health", host, 60)')
        < source.index("resume_workspace(control, host_url, WORKSPACE_ID)")
        < source.index("worker = _start_process(")
    )
    assert "CodeDispatchService(settings, broker, store, job_db).enqueue(" in source
    agent_source = AGENT.read_text(encoding="utf-8")
    assert "AgentDispatchService(settings, broker, store)" in agent_source
    assert "profile_from_node(node)" in agent_source
    assert 'profile_source="node"' in agent_source
    assert "LocalSkillManager" not in agent_source
    assert '"worker/executor.py", "--config"' in source
    assert "server.app.main:create_app" in source
    assert "agent_execution_requests" in source
    assert '"worker-code-allow"' in source
    assert 'config={"probe": json.dumps(config)}' in source
    assert "worker_before + 2" in source
    assert "_verify_agent_events(" in source
    assert "_preserve_payload_evidence(state_dir, output_dir)" in source
    assert "execute_code(" not in source
