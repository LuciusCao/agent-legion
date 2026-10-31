"""Static fail-closed contracts for the native Ubuntu spike (no host changes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.e2e.native_worker_spike_payload import REQUIRED_CHECKS, check_failures

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "scripts" / "e2e" / "native_worker_spike.service"
SCRIPT = ROOT / "scripts" / "e2e" / "native_worker_spike.sh"
HARNESS = ROOT / "scripts" / "e2e" / "native_worker_spike.py"
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


def test_ci_systemd_install_is_explicitly_guarded() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "CI:-} != true" in source
    assert "NATIVE_SPIKE_ALLOW_SYSTEMD_INSTALL:-} != 1" in source
    assert "systemctl start --no-block" in source
    assert "systemd-runtime.txt" in source
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "sudo --preserve-env=CI,NATIVE_SPIKE_ALLOW_SYSTEMD_INSTALL" in workflow
    assert "systemd-analyze verify" in workflow
    assert "actions/upload-artifact@" in workflow


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


def test_worker_path_uses_real_dispatch_claim_and_process_entrypoints() -> None:
    source = HARNESS.read_text(encoding="utf-8")
    assert "CodeDispatchService(settings, broker, store, job_db).enqueue(" in source
    assert "AgentDispatchService(settings, broker, store)" in source
    assert '"worker/executor.py", "--config"' in source
    assert "server.app.main:create_app" in source
    assert "agent_execution_requests" in source
    assert '"worker-code-allow"' in source
    assert 'config={"probe": json.dumps(config)}' in source
    assert "worker_before + 2" in source
    assert "_verify_agent_events(" in source
    assert "_preserve_payload_evidence(state_dir, output_dir)" in source
    assert "execute_code(" not in source
