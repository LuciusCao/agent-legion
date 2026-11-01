"""Fail-closed contracts for the disposable native Ubuntu network probe."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.e2e import native_sandbox_probe as probe_module
from scripts.e2e.native_sandbox_probe import (
    ALL_CASES,
    N0_CASES,
    NativeSandboxProbe,
    _os_release_value,
)
from scripts.e2e.native_sandbox_probe_collect import _case_audit, _sanitize
from scripts.e2e.native_sandbox_probe_payload import E5_CHECKS, check_failures

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "native-sandbox-probe.yml"
UNIT = ROOT / "scripts" / "e2e" / "native_sandbox_probe.service"
SCRIPT = ROOT / "scripts" / "e2e" / "native_sandbox_probe.sh"
HARNESS = ROOT / "scripts" / "e2e" / "native_sandbox_probe.py"
PAYLOAD = ROOT / "scripts" / "e2e" / "native_sandbox_probe_payload.py"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


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


def _bare_probe(tmp_path: Path) -> NativeSandboxProbe:
    probe = NativeSandboxProbe.__new__(NativeSandboxProbe)
    probe.context = "n0"
    probe.work_dir = tmp_path / "work"
    probe.cases_dir = tmp_path / "evidence" / "cases"
    probe.output_dir = tmp_path / "evidence"
    probe.parent_namespaces = {name: f"{name}:[1]" for name in ("user", "mnt", "pid", "net")}
    probe.canary = tmp_path / "canary"
    probe.listener = SimpleNamespace(host="127.0.0.1", port=12345)
    return probe


def test_workflow_runs_on_branch_push_at_the_current_sha() -> None:
    workflow = _workflow()
    triggers = workflow[True]
    assert triggers["push"]["branches"] == ["feat/084-native-sandbox-network-probe-39"]
    assert set(triggers["pull_request"]["paths"]) == {
        ".github/workflows/native-sandbox-probe.yml",
        "scripts/e2e/native_sandbox_probe*",
        "tests/e2e/native_sandbox_probe*",
    }
    job = workflow["jobs"]["native-probe"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert "services" not in job and "image" not in job
    assert workflow["permissions"] == {"contents": "read"}
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "Build the current-SHA velites sandbox" in source
    assert 'summary["git_sha"] == os.environ["GITHUB_SHA"]' in source
    assert 'assert summary["harness_complete"] is True' in source
    assert 'assert summary["production_fixed"] is False' in source
    assert "actions/upload-artifact@" in source


def test_n0_unit_matches_the_established_nonroot_constraints() -> None:
    settings = _unit_settings()
    assert settings["Type"] == "oneshot"
    assert settings["User"] == "agent-legion-sandbox-probe"
    assert settings["Group"] == "agent-legion-sandbox-probe"
    assert settings["NoNewPrivileges"] == "yes"
    assert settings["CapabilityBoundingSet"] == ""
    assert settings["AmbientCapabilities"] == ""
    assert settings["SecureBits"].startswith("noroot noroot-locked")
    assert settings["ProtectSystem"] == "strict"
    assert settings["ReadWritePaths"] == "/var/lib/agent-legion-native-sandbox-probe"
    assert settings["ProtectKernelLogs"] == "yes"
    assert "PrivateNetwork" not in settings
    assert "--summarize" in settings["ExecStart"]
    for case in N0_CASES:
        assert case in settings["ExecStart"]


def test_no_privilege_network_or_lifecycle_escape_hatches() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (WORKFLOW, UNIT, SCRIPT, HARNESS, PAYLOAD)
    ).lower()
    for forbidden in (
        "sys_admin",
        "net_admin",
        "sys_ptrace",
        "cap_add",
        "privileged: true",
        "setcap",
        "chmod u+s",
        "sysctl -w",
        "aa-disable",
        "scm_rights",
        "--share-net",
        "--allow-network",
        "--as-pid-1",
        "docker run",
    ):
        assert forbidden not in source
    assert "strace" in source
    assert "apparmor" in source
    collector = (ROOT / "scripts" / "e2e" / "native_sandbox_probe_collect.py").read_text()
    assert '["aa-status", "--json"]' in collector
    assert 'shutil.which("aa-enabled")' in source
    assert "/usr/sbin/aa-enabled" not in source
    assert "close_fds=true" in source
    assert "pass_fds" not in source


def test_matrix_contains_independent_e0_through_e5_cases() -> None:
    assert len(ALL_CASES) == len(set(ALL_CASES)) == 15
    assert {case[:2] for case in ALL_CASES} == {"e0", "e1", "e2", "e3", "e4", "e5"}
    assert sum(case.startswith("e4-") for case in ALL_CASES) == 4
    assert sum(case.startswith("e5-") for case in ALL_CASES) == 3
    assert "e0-runuser-current-no-network" in ALL_CASES
    assert "e0-n0-bwrap-no-net-control" in ALL_CASES
    assert "e3-n0-raw-netlink-trace" in ALL_CASES


def test_e5_command_is_per_call_private_net_with_original_identity(tmp_path: Path) -> None:
    probe = _bare_probe(tmp_path)
    command, _ = probe._command("e5-n0-single")
    assert command[:7] == [
        "unshare",
        "--user",
        "--map-root-user",
        "--net",
        "--",
        "bwrap",
        "--die-with-parent",
    ]
    assert command.count("--unshare-net") == 0
    assert command.count("--unshare-user") == 1
    uid_index = command.index("--uid")
    gid_index = command.index("--gid")
    assert command[uid_index + 1] == str(probe_module.os.getuid())
    assert command[gid_index + 1] == str(probe_module.os.getgid())
    cap_index = command.index("--cap-drop")
    assert command[cap_index + 1] == "ALL"
    assert "--unshare-pid" in command and "--proc" in command
    assert "--as-pid-1" not in command


def test_e4_is_single_variable_and_never_a_delivery_candidate() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "run_e4_case e4-nnp-yes-cap-empty yes empty" in source
    assert "run_e4_case e4-nnp-no-cap-empty no empty" in source
    assert "run_e4_case e4-nnp-yes-cap-default yes default" in source
    assert "run_e4_case e4-nnp-no-cap-default no default" in source
    assert "if [[ $cap_mode == empty ]]" in source
    harness = HARNESS.read_text(encoding="utf-8")
    assert '"diagnostic_only": case.startswith("e4-")' in harness
    assert '"delivery_candidate": case.startswith("e5-")' in harness


def _run_fake_matrix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, e1_outcome: str) -> tuple:
    calls = []
    results = {}

    class FakeProbe:
        def __init__(self, **kwargs):
            self.results = results

        def execute(self, case):
            calls.append(("execute", case))
            outcome = e1_outcome if case == "e1-n0-user-net-namespace" else "passed"
            self.results[case] = {"outcome": outcome}
            return self.results[case]

        def skip(self, case, prerequisite):
            calls.append(("skip", case, prerequisite))
            self.results[case] = {"outcome": "skipped", "prerequisite": prerequisite}
            return self.results[case]

        def close(self):
            calls.append(("close",))

        def summarize(self):
            return {"decision": "no-go", "reason_codes": []}

    monkeypatch.setattr(probe_module, "NativeSandboxProbe", FakeProbe)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "native_sandbox_probe.py",
            "--state-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "evidence"),
            "--context",
            "n0",
            "--cases",
            ",".join(N0_CASES),
            "--summarize",
        ],
    )
    assert probe_module.main() == 0
    return calls, results


def test_e2_and_e5_run_only_after_their_prerequisites(monkeypatch, tmp_path) -> None:
    calls, results = _run_fake_matrix(monkeypatch, tmp_path, "passed")
    executed = [call[1] for call in calls if call[0] == "execute"]
    assert executed.index("e1-n0-user-net-namespace") < executed.index("e2-n0-private-loopback")
    assert {case for case in executed if case.startswith("e5-")} == {
        "e5-n0-single",
        "e5-n0-concurrent-a",
        "e5-n0-concurrent-b",
    }
    assert not [call for call in calls if call[0] == "skip"]
    assert results["e3-n0-raw-netlink-trace"]["outcome"] == "passed"


def test_expected_prerequisite_failure_does_not_stop_diagnostics(monkeypatch, tmp_path) -> None:
    calls, results = _run_fake_matrix(monkeypatch, tmp_path, "failed")
    executed = [call[1] for call in calls if call[0] == "execute"]
    assert "e3-n0-raw-netlink-trace" in executed
    assert "e2-n0-private-loopback" not in executed
    assert not any(case.startswith("e5-") for case in executed)
    skipped = [call for call in calls if call[0] == "skip"]
    assert len(skipped) == 4
    assert results["e2-n0-private-loopback"]["prerequisite"] == "e1-n0-user-net-namespace"
    assert calls[-1] == ("close",)


def test_skip_baseline_failure_is_evidence_not_a_matrix_abort(monkeypatch, tmp_path) -> None:
    probe = _bare_probe(tmp_path)
    probe.results = {}

    def missing_aa_enabled():
        raise FileNotFoundError("aa-enabled")

    monkeypatch.setattr(probe, "_baseline", missing_aa_enabled)
    result = probe.skip("e5-n0-single", "e1-n0-user-net-namespace")
    assert result["outcome"] == "skipped"
    assert result["evidence_collected"] is False
    assert result["baseline_error"] == "FileNotFoundError: aa-enabled"
    assert probe.results["e5-n0-single"] is result


def _summary_probe(tmp_path: Path) -> NativeSandboxProbe:
    probe = _bare_probe(tmp_path)
    probe.git_sha = "a" * 40
    probe.results = {
        case: {
            "case": case,
            "outcome": "passed",
            "evidence_collected": True,
            "started_epoch": 1.0,
            "ended_epoch": 2.0,
        }
        for case in ALL_CASES
    }
    return probe


def test_summary_go_requires_every_e5_case_and_never_claims_production_fixed(
    tmp_path: Path,
) -> None:
    probe = _summary_probe(tmp_path)
    summary = probe.summarize()
    assert summary["decision"] == "go"
    assert summary["production_fixed"] is False
    assert summary["issues"] == {"39": "go", "1049": "go"}
    probe.results["e5-n0-concurrent-b"]["outcome"] = "skipped"
    summary = probe.summarize()
    assert summary["decision"] == "no-go"
    assert "E5_NOT_PASSED:e5-n0-concurrent-b:skipped" in summary["reason_codes"]
    assert summary["production_fixed"] is False


def test_summary_no_go_is_not_an_incomplete_or_all_green_harness(tmp_path: Path) -> None:
    probe = _summary_probe(tmp_path)
    for case in ALL_CASES:
        if case.startswith("e5-"):
            probe.results[case]["outcome"] = "skipped"
    summary = probe.summarize()
    assert summary["harness_complete"] is True
    assert summary["decision"] == "no-go"
    assert len(summary["cases"]) == 15


def test_summary_infrastructure_error_is_no_go_even_when_e5_passes(tmp_path: Path) -> None:
    probe = _summary_probe(tmp_path)
    probe.results["e3-n0-raw-netlink-trace"]["evidence_collected"] = False
    summary = probe.summarize()
    assert summary["harness_complete"] is False
    assert summary["decision"] == "no-go"
    assert "CASE_INFRASTRUCTURE_ERROR:e3-n0-raw-netlink-trace" in summary["reason_codes"]


def test_every_e5_payload_boundary_is_mandatory_and_mutation_detected() -> None:
    document = {"checks": {name: True for name in E5_CHECKS}}
    assert check_failures(document) == []
    for name in E5_CHECKS:
        document["checks"][name] = False
        assert check_failures(document) == [name]
        document["checks"][name] = True
    del document["checks"]["default_reaper"]
    assert "default_reaper" in check_failures(document)


def test_default_reaper_and_descendant_contract_do_not_require_payload_pid1() -> None:
    source = PAYLOAD.read_text(encoding="utf-8")
    assert '"initial_payload_pid2": os.getpid() == 2' in source
    assert '"default_reaper": "bwrap" in document["reaper"]' in source
    assert 'document["descendant"]["pid"] != 1' in source
    assert "os.getpid() == 1" not in source
    assert "--as-pid-1" not in source


def test_os_release_parser_does_not_confuse_version_id_with_id(monkeypatch) -> None:
    monkeypatch.setattr(
        probe_module,
        "_read",
        lambda _path: 'NAME="Ubuntu"\nVERSION_ID="24.04"\nID="ubuntu"',
    )
    assert _os_release_value("ID") == "ubuntu"
    assert _os_release_value("VERSION_ID") == "24.04"


def test_audit_window_allows_delayed_kernel_journal_delivery(monkeypatch) -> None:
    calls = []

    def fake_journal(*args):
        calls.append(args)
        return 'kernel: apparmor="DENIED" operation="capable"\n'

    monkeypatch.setitem(_case_audit.__globals__, "_journal", fake_journal)
    raw = _case_audit({"started_epoch": 100.0, "ended_epoch": 101.0})
    assert 'apparmor="DENIED"' in raw
    assert calls == [("-k", "--since", "@99.500000", "--until", "@103.000000")]


def test_evidence_sanitization_removes_probe_canaries() -> None:
    sanitized = _sanitize("native-sandbox-probe-parent-canary native-sandbox-probe-canary")
    assert "canary" not in sanitized.replace("[redacted-probe-canary]", "")
    assert json.dumps(sanitized)
