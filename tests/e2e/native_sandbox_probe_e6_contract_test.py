"""Mutation coverage for the targeted AppArmor direct-bwrap candidate contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.e2e import native_sandbox_probe_apparmor as apparmor
from scripts.e2e.native_sandbox_probe_e6 import E6Probe
from scripts.e2e.native_sandbox_probe_e6_contract import (
    CLEANUP_MODES,
    E6_CASES,
    LISTENERS,
    validate_e6_independence,
    validate_e6_result,
    validate_rollback,
)
from scripts.e2e.native_sandbox_probe_e6_payload import (
    E6_PAYLOAD_CHECKS,
    _host_mounts_hidden,
)

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "native-sandbox-probe.yml"
STOCK_UNIT = ROOT / "scripts" / "e2e" / "native_sandbox_probe.service"
E6_UNIT = ROOT / "scripts" / "e2e" / "native_sandbox_probe_e6@.service"


def _cleanup_trials() -> list[dict]:
    return [
        {
            "mode": mode,
            "launcher_pid": 20000 + index,
            "observed_pids": [20000 + index, 30000 + index],
            "expected_signal_used": True,
            "process_group_cleaned": True,
            "processes_reaped": True,
            "namespaces_released": True,
        }
        for index, mode in enumerate(CLEANUP_MODES)
    ]


def _passed_result(case: str) -> dict:
    execution = {
        "returncode": 0,
        "timed_out": False,
        "process_group_cleaned": True,
        "listener_connections_before": 0,
        "listener_connections_after": 0,
        "launcher_pid": 12345,
        "payload_started": True,
        "bwrap_main": {
            "pid": 12345,
            "apparmor_current": "bwrap (enforce)",
            "status": {"NoNewPrivs": "1"},
        },
        "candidate_listeners": {name: {"before": 0, "after": 0} for name in LISTENERS},
        "command_log": f"cases/{case}/command.log",
        "stderr_tail": "",
    }
    if case == "e6-n0-single":
        execution["cleanup_trials"] = _cleanup_trials()
    return {
        "case": case,
        "outcome": "passed",
        "evidence_collected": True,
        "baseline": {"git_sha": "a" * 40},
        "started_epoch": 1.0,
        "ended_epoch": 2.0,
        "execution": execution,
        "payload": {
            "case": case,
            "checks": {name: True for name in E6_PAYLOAD_CHECKS},
            "namespaces": {name: f"{name}:[{case}]" for name in ("user", "mnt", "pid", "net")},
        },
    }


def _rollback_result(case: str) -> dict:
    result = _passed_result(case)
    result["outcome"] = "failed"
    result["expected_failure"] = True
    result["execution"]["returncode"] = 1
    result["execution"]["payload_started"] = False
    del result["payload"]
    return result


def _summary_probe(tmp_path: Path, phase: str = "targeted") -> E6Probe:
    probe = E6Probe.__new__(E6Probe)
    probe.phase = phase
    probe.git_sha = "a" * 40
    probe.output_dir = tmp_path
    probe.results = {
        case: _passed_result(case) if phase == "targeted" else _rollback_result(case)
        for case in E6_CASES
    }
    return probe


def _bare_probe(tmp_path: Path) -> E6Probe:
    probe = E6Probe.__new__(E6Probe)
    probe.context = "n0"
    probe.phase = "targeted"
    probe.work_dir = tmp_path / "work"
    probe.output_dir = tmp_path / "evidence"
    probe.cases_dir = probe.output_dir / "cases"
    probe.state_dir = tmp_path / "state"
    probe.parent_namespaces = {name: f"{name}:[1]" for name in ("user", "mnt", "pid", "net")}
    probe.canary = tmp_path / "canary"
    probe.listener = SimpleNamespace(host="127.0.0.1", port=10001)
    probe.nonloopback_host = "192.0.2.10"
    probe.nonloopback_listener = SimpleNamespace(port=10002)
    probe.abstract_listener = SimpleNamespace(name="native-test")
    probe._baseline = lambda: {"process": {"status": {"Seccomp": "2"}}}
    return probe


def _unit_settings(path: Path) -> dict[str, str]:
    settings = {}
    in_service = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "[Service]":
            in_service = True
        elif line.startswith("["):
            in_service = False
        elif in_service and line and not line.startswith("#"):
            key, value = line.split("=", 1)
            settings[key] = value
    return settings


def test_e6_is_direct_exact_bwrap_with_private_namespaces_and_original_identity(
    tmp_path: Path,
) -> None:
    probe = _bare_probe(tmp_path)
    command, _ = probe._command("e6-n0-single")
    assert command[0] == "/usr/bin/bwrap"
    assert command[0] != "unshare" and "unshare" not in command
    assert command.count("--unshare-user") == 1
    assert command.count("--unshare-pid") == 1
    assert command.count("--unshare-net") == 1
    assert command[command.index("--uid") + 1] == str(os.getuid())
    assert command[command.index("--gid") + 1] == str(os.getgid())
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "--die-with-parent" in command and "--as-pid-1" not in command


def test_mountinfo_allows_only_the_explicit_work_bind_inside_the_state_dir() -> None:
    config = {
        "state_dir": "/var/lib/probe",
        "work_path": "/var/lib/probe/work/e6-n0-single",
        "canary_path": "/var/lib/probe/parent-canary",
    }
    allowed = "100 99 8:1 / /var/lib/probe/work/e6-n0-single rw - ext4 /dev/sda rw"
    assert _host_mounts_hidden(config, allowed) is True
    assert (
        _host_mounts_hidden(
            config, allowed + "\n101 100 8:1 / /var/lib/probe/parent-canary ro - ext4 /dev/sda ro"
        )
        is False
    )
    assert (
        _host_mounts_hidden(
            config, allowed + "\n101 100 8:1 / /var/lib/probe/secrets ro - ext4 /dev/sda ro"
        )
        is False
    )


def test_candidate_unit_preserves_every_n0_security_constraint() -> None:
    stock = _unit_settings(STOCK_UNIT)
    candidate = _unit_settings(E6_UNIT)
    for key in (
        "User",
        "Group",
        "NoNewPrivileges",
        "CapabilityBoundingSet",
        "AmbientCapabilities",
        "SecureBits",
        "ProtectSystem",
        "ReadWritePaths",
        "ProtectKernelLogs",
        "RestrictSUIDSGID",
        "LockPersonality",
    ):
        assert candidate[key] == stock[key]
    assert "--phase %i" in candidate["ExecStart"]
    assert "PrivateNetwork" not in candidate


def test_workflow_is_disposable_a_b_r_with_separate_candidate_artifacts() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["native-probe"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert workflow["permissions"] == {"contents": "read"}
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "apparmor-profiles" in source
    assert "native_sandbox_probe_apparmor.py enable" in source
    assert "native_sandbox_probe_apparmor.py disable" in source
    assert "run-candidate targeted" in source and "run-candidate rollback" in source
    assert 'candidate["decision"] == "go"' in source
    assert 'candidate["rollback_restored"] is True' in source
    assert 'stock["production_fixed"] is False' in source
    for phase in ("stock", "targeted", "rollback"):
        assert f"native-sandbox-probe-{phase}-${{{{ github.run_id }}}}" in source


def test_only_reviewed_double_profile_and_managed_rollback_are_permitted() -> None:
    source = (ROOT / "scripts" / "e2e" / "native_sandbox_probe_apparmor.py").read_text()
    assert Path("/usr/share/apparmor/extra-profiles/bwrap-userns-restrict") == apparmor.SOURCE
    assert Path("/etc/apparmor.d/bwrap-userns-restrict") == apparmor.MANAGED
    assert len(apparmor.EXPECTED_SOURCE_SHA256) == 64
    assert apparmor.EXPECTED_SOURCE_SHA256 in source
    assert '["apparmor_parser", "-Q", str(MANAGED)]' in source
    assert '["apparmor_parser", "-r", str(MANAGED)]' in source
    assert '["apparmor_parser", "-R", str(MANAGED)]' in source
    assert "unshare-userns-restrict" not in source
    assert "AppArmorProfile" not in source
    assert "aa-disable" not in source and "sysctl -w" not in source


def test_source_drift_stops_before_profile_installation(monkeypatch, tmp_path: Path) -> None:
    drifted = tmp_path / "bwrap-userns-restrict"
    drifted.write_text(
        "profile bwrap /usr/bin/bwrap flags=(attach_disconnected) {\n"
        " allow capability,\n allow px /** -> bwrap//&unpriv_bwrap,\n}\n"
        "profile unpriv_bwrap flags=(attach_disconnected) {\n"
        " allow pix /** -> &unpriv_bwrap,\n audit deny capability,\n}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(apparmor, "SOURCE", drifted)
    monkeypatch.setattr(apparmor, "_package_version", lambda package: "reviewed")
    with pytest.raises(RuntimeError, match="source hash drift"):
        apparmor._require_source()


@pytest.mark.parametrize("mutation", ["delete", "false"])
def test_every_e6_payload_evidence_mutation_fails_result_and_summary(tmp_path, mutation) -> None:
    for name in E6_PAYLOAD_CHECKS:
        result = _passed_result("e6-n0-single")
        if mutation == "delete":
            del result["payload"]["checks"][name]
        else:
            result["payload"]["checks"][name] = False
        assert name in validate_e6_result("e6-n0-single", result), (mutation, name)
        probe = _summary_probe(tmp_path)
        probe.results["e6-n0-single"] = result
        summary = probe.summarize()
        assert summary["decision"] == "no-go", (mutation, name)
        assert "E6_EVIDENCE_INVALID:e6-n0-single" in summary["reason_codes"]
        assert summary["production_fixed"] is False


@pytest.mark.parametrize("namespace", ("user", "mnt", "pid", "net"))
def test_namespace_missing_and_duplicate_evidence_fails_closed(tmp_path, namespace) -> None:
    result = _passed_result("e6-n0-single")
    del result["payload"]["namespaces"][namespace]
    assert f"namespace_{namespace}" in validate_e6_result("e6-n0-single", result)
    probe = _summary_probe(tmp_path)
    probe.results["e6-n0-single"] = result
    assert probe.summarize()["decision"] == "no-go"

    results = {case: _passed_result(case) for case in E6_CASES}
    results["e6-n0-single"]["payload"]["namespaces"][namespace] = results["e6-n0-concurrent-a"][
        "payload"
    ]["namespaces"][namespace]
    assert f"duplicate_namespace_{namespace}" in validate_e6_independence(results)
    probe = _summary_probe(tmp_path)
    probe.results = results
    assert probe.summarize()["decision"] == "no-go"


def test_apparmor_main_identity_and_handshake_mutations_fail_closed(tmp_path) -> None:
    mutations = (
        ("apparmor_current", "unconfined"),
        ("pid", 999),
        ("status", {"NoNewPrivs": "0"}),
    )
    for field, value in mutations:
        result = _passed_result("e6-n0-single")
        result["execution"]["bwrap_main"][field] = value
        assert validate_e6_result("e6-n0-single", result)
        probe = _summary_probe(tmp_path)
        probe.results["e6-n0-single"] = result
        assert probe.summarize()["decision"] == "no-go"
    for value in (False, None, "true"):
        result = _passed_result("e6-n0-single")
        result["execution"]["payload_started"] = value
        assert "payload_started" in validate_e6_result("e6-n0-single", result)


@pytest.mark.parametrize("listener", LISTENERS)
def test_every_host_listener_connection_mutation_fails_closed(listener) -> None:
    result = _passed_result("e6-n0-single")
    result["execution"]["candidate_listeners"][listener]["after"] = 1
    assert f"candidate_listener_{listener}" in validate_e6_result("e6-n0-single", result)
    del result["execution"]["candidate_listeners"][listener]
    assert f"candidate_listener_{listener}" in validate_e6_result("e6-n0-single", result)


@pytest.mark.parametrize("mode", CLEANUP_MODES)
@pytest.mark.parametrize(
    "field",
    (
        "expected_signal_used",
        "process_group_cleaned",
        "processes_reaped",
        "namespaces_released",
    ),
)
def test_every_cleanup_class_mutation_fails_result_and_summary(tmp_path, mode, field) -> None:
    result = _passed_result("e6-n0-single")
    trial = next(row for row in result["execution"]["cleanup_trials"] if row["mode"] == mode)
    trial[field] = False
    assert f"{mode}_{field}" in validate_e6_result("e6-n0-single", result)
    probe = _summary_probe(tmp_path)
    probe.results["e6-n0-single"] = result
    assert probe.summarize()["decision"] == "no-go"


@pytest.mark.parametrize("mode", CLEANUP_MODES)
def test_cleanup_requires_a_reaped_process_tree_and_valid_launcher(mode) -> None:
    for field, value in (("observed_pids", [12345]), ("launcher_pid", 0)):
        result = _passed_result("e6-n0-single")
        trial = next(row for row in result["execution"]["cleanup_trials"] if row["mode"] == mode)
        trial[field] = value
        assert validate_e6_result("e6-n0-single", result)


def test_cleanup_fault_injection_runs_signal_timeout_and_parent_death(
    monkeypatch, tmp_path
) -> None:
    calls = []

    def fake_signal(prefix, work, mode, *, env):
        assert env == {"PATH": "/usr/bin"}
        calls.append(mode)
        return {"mode": mode}

    def fake_parent(prefix, work, *, env):
        assert env == {"PATH": "/usr/bin"}
        calls.append("parent-death")
        return {"mode": "parent-death"}

    cleanup_module = __import__(
        "scripts.e2e.native_sandbox_probe_e6_cleanup", fromlist=["run_cleanup_trials"]
    )
    monkeypatch.setattr(cleanup_module, "run_signal_or_timeout", fake_signal)
    monkeypatch.setattr(cleanup_module, "run_parent_death", fake_parent)
    result = cleanup_module.run_cleanup_trials(
        ["/usr/bin/bwrap", "--"], tmp_path, env={"PATH": "/usr/bin"}
    )
    assert calls == ["signal", "timeout", "parent-death"]
    assert [row["mode"] for row in result] == calls


def test_candidate_summary_is_go_only_with_complete_independent_e6(tmp_path) -> None:
    summary = _summary_probe(tmp_path).summarize()
    assert summary["decision"] == "go"
    assert summary["candidate_only"] is True
    assert summary["production_fixed"] is False
    assert summary["issues"] == {"39": "candidate-only", "1049": "not-assessed"}
    assert len(summary["cases"]) == 3
    saved = json.loads((tmp_path / "candidate-summary.json").read_text(encoding="utf-8"))
    assert saved == summary


def test_first_candidate_failure_stops_without_permuting_flags() -> None:
    probe = E6Probe.__new__(E6Probe)
    probe.phase = "targeted"
    calls = []
    probe.results = {}
    probe.execute = lambda case: calls.append(case) or {"outcome": "failed"}
    probe.run()
    assert calls == ["e6-n0-single"]


def test_rollback_requires_all_pre_payload_failures_and_clean_process_groups(tmp_path) -> None:
    probe = _summary_probe(tmp_path, "rollback")
    summary = probe.summarize()
    assert summary["decision"] == "no-go"
    assert summary["rollback_restored"] is True
    assert summary["rollback_failures"] == []
    assert summary["production_fixed"] is False

    for mutation in ("success", "payload_started", "payload", "cleanup", "missing"):
        results = {case: _rollback_result(case) for case in E6_CASES}
        if mutation == "success":
            results["e6-n0-single"]["execution"]["returncode"] = 0
        elif mutation == "payload_started":
            results["e6-n0-single"]["execution"]["payload_started"] = True
        elif mutation == "payload":
            results["e6-n0-single"]["payload"] = {"case": "e6-n0-single"}
        elif mutation == "cleanup":
            results["e6-n0-single"]["execution"]["process_group_cleaned"] = False
        else:
            del results["e6-n0-single"]
        assert validate_rollback(results), mutation


def test_summary_rejects_missing_and_infrastructure_error_cases(tmp_path) -> None:
    probe = _summary_probe(tmp_path)
    del probe.results["e6-n0-concurrent-a"]
    summary = probe.summarize()
    assert summary["harness_complete"] is False and summary["decision"] == "no-go"
    assert "CASE_NOT_RECORDED:e6-n0-concurrent-a" in summary["reason_codes"]

    probe = _summary_probe(tmp_path)
    probe.results["e6-n0-concurrent-a"]["evidence_collected"] = False
    summary = probe.summarize()
    assert summary["harness_complete"] is False
    assert "CASE_INFRASTRUCTURE_ERROR:e6-n0-concurrent-a" in summary["reason_codes"]
