"""Adversarial reproductions for the M144 raw-evidence and rollback contracts."""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.e2e import native_sandbox_probe_apparmor as apparmor
from scripts.e2e import native_sandbox_probe_audit as audit_module
from scripts.e2e import native_sandbox_probe_quiesce as quiesce_module
from scripts.e2e.native_sandbox_probe import ProbeListener
from scripts.e2e.native_sandbox_probe_e6 import E6Probe
from scripts.e2e.native_sandbox_probe_e6_contract import validate_e6_result
from scripts.e2e.native_sandbox_probe_e6_evidence import TOP_LEVEL_FIELDS
from scripts.e2e.native_sandbox_probe_e6_listeners import positive_controls
from tests.e2e.native_sandbox_probe_e6_contract_test import (
    CLEANUP_MODES,
    LISTENERS,
    WORKFLOW,
    _matrix_probe,
    _passed_result,
    _summary_probe,
)

pytestmark = pytest.mark.no_db


def test_complete_raw_fixture_passes_real_execute_main_and_summary(tmp_path) -> None:
    probe = _matrix_probe(tmp_path)
    assert probe.execute("e6-n0-single")["outcome"] == "passed"
    probe = _matrix_probe(tmp_path)
    probe.run()
    summary = probe.summarize()
    assert summary["decision"] == "go"
    assert summary["harness_complete"] is True


def _assert_execute_main_summary_reject(tmp_path, mutate) -> None:
    probe = _matrix_probe(tmp_path, mutate)
    result = probe.execute("e6-n0-single")
    probe._add_cleanup(result)
    assert result["outcome"] == "failed"
    assert result["invariant_failures"]

    probe = _matrix_probe(tmp_path, mutate)
    probe.run()
    main_summary = probe.summarize()
    assert main_summary["decision"] == "no-go"
    assert main_summary["harness_complete"] is False

    summary_probe = _summary_probe(tmp_path)
    mutate(summary_probe.results)
    summary = summary_probe.summarize()
    assert summary["decision"] == "no-go"
    assert "E6_EVIDENCE_INVALID:e6-n0-single" in summary["reason_codes"]


@pytest.mark.parametrize("field", TOP_LEVEL_FIELDS)
@pytest.mark.parametrize("mutation", ["delete", "empty"])
def test_raw_field_delete_and_empty_mutations_fail_all_three_layers(
    tmp_path, field, mutation
) -> None:
    def mutate(results):
        if mutation == "delete":
            del results["e6-n0-single"]["payload"][field]
        else:
            results["e6-n0-single"]["payload"][field] = None

    _assert_execute_main_summary_reject(tmp_path, mutate)


RAW_ATTACKS = (
    ("identity", {"uid": 0, "euid": 0, "gid": 999, "egid": 999, "pid": 2, "ppid": 1}),
    (
        "status",
        {
            "NoNewPrivs": "1",
            "Seccomp": "2",
            "CapInh": "0",
            "CapPrm": "0",
            "CapEff": "1",
            "CapBnd": "0",
            "CapAmb": "0",
        },
    ),
    ("apparmor", {"current": "unconfined"}),
    ("host_network", {name: {"connected": True, "detail": "connected"} for name in LISTENERS}),
    ("mountinfo", "100 99 8:1 / /var/lib/probe/parent-canary ro - ext4 /dev/sda ro"),
    ("uid_map", "0 999 1"),
    ("gid_map", "0 999 1"),
    ("reaper", "systemd"),
    ("descendant", {"returncode": 0, "pid": 1, "reaper_visible": True}),
    ("parent_proc_visible", True),
    ("parent_secret_present", True),
    ("cwd_writable_observed", False),
    ("canary", {"read_denied": False, "write_denied": True}),
    ("stdio", {"observed": [0, 1, 2, 3, 4], "enumeration_fds": [3]}),
    (
        "mount_escape",
        {"attempts": [{"returncode": 0}, {"returncode": 1}, {"returncode": 1}], "denied": False},
    ),
    ("handshake", {"release_observed": False}),
    ("interfaces", ["lo", "eth0"]),
    ("loopback", {"flags": 0, "ipv4": None}),
)


@pytest.mark.parametrize("field,value", RAW_ATTACKS)
def test_raw_check_inconsistency_fails_all_three_layers(tmp_path, field, value) -> None:
    def mutate(results):
        results["e6-n0-single"]["payload"][field] = value

    _assert_execute_main_summary_reject(tmp_path, mutate)


def test_namespace_reuse_raw_evidence_fails_all_three_layers(tmp_path) -> None:
    def mutate(results):
        results["e6-n0-single"]["payload"]["namespaces"]["user"] = "user:[1]"

    _assert_execute_main_summary_reject(tmp_path, mutate)


@pytest.mark.parametrize("mode", CLEANUP_MODES)
@pytest.mark.parametrize("mutation", ["delete", "empty", "wrong"])
def test_cleanup_namespace_path_evidence_is_mandatory(tmp_path, mode, mutation) -> None:
    def mutate(results):
        execution = results["e6-n0-single"]["execution"]
        trial = next(row for row in execution["cleanup_trials"] if row["mode"] == mode)
        if mutation == "delete":
            del trial["namespace_paths"]
        elif mutation == "empty":
            trial["namespace_paths"] = []
        else:
            trial["namespace_paths"] = trial["namespace_paths"][:-1]

    _assert_execute_main_summary_reject(tmp_path, mutate)


@pytest.mark.parametrize("listener", LISTENERS)
@pytest.mark.parametrize("control", ["before_control", "after_control"])
@pytest.mark.parametrize("mutation", ["dead", "wrong_token", "bad_count"])
def test_listener_positive_control_mutation_fails_candidate_and_summary(
    tmp_path, listener, control, mutation
) -> None:
    result = _passed_result("e6-n0-single")
    row = result["execution"]["candidate_listeners"][listener][control]
    if mutation == "dead":
        row["connected"] = False
    elif mutation == "wrong_token":
        row["token_valid"] = False
    else:
        row["after"] = row["before"]
    assert validate_e6_result("e6-n0-single", result)
    probe = _summary_probe(tmp_path)
    probe.results["e6-n0-single"] = result
    assert probe.summarize()["decision"] == "no-go"


def test_parent_tcp_token_controls_are_real_and_dead_listener_fails(monkeypatch) -> None:
    loopback = ProbeListener()
    nonloopback = ProbeListener()
    counts = iter((0, 1))
    probe = E6Probe.__new__(E6Probe)
    probe._case_listeners = {
        "case": {
            "loopback": loopback,
            "nonloopback": nonloopback,
            "abstract_unix": SimpleNamespace(name="static-only", count=lambda: next(counts)),
        }
    }
    monkeypatch.setattr(
        probe,
        "_abstract_control",
        lambda case: {"connected": True, "token_valid": True, "detail": "static-contract"},
    )
    try:
        controls = probe._positive_controls("case")
        assert all(row["token_valid"] is True for row in controls.values())
        assert all(row["after"] - row["before"] == 1 for row in controls.values())
        loopback.close()
        with pytest.raises(RuntimeError, match="positive control failed"):
            probe._positive_controls("case")
    finally:
        nonloopback.close()


def test_concurrent_listener_controls_are_isolated_with_forced_interleaving(monkeypatch) -> None:
    probe = E6Probe.__new__(E6Probe)
    probe._case_listeners = {}
    actual_tcp = probe._control_connection
    barrier = threading.Barrier(2)
    for case in ("a", "b"):
        abstract = SimpleNamespace(name=f"abstract-{case}", value=0)
        abstract.count = lambda abstract=abstract: abstract.value
        probe._case_listeners[case] = {
            "loopback": ProbeListener(),
            "nonloopback": ProbeListener(),
            "abstract_unix": abstract,
        }

    def interleaved(host, port):
        barrier.wait(timeout=30)
        return actual_tcp(host, port)

    def abstract_attempt(case):
        listener = probe._case_listeners[case]["abstract_unix"]
        listener.value += 1
        return {"connected": True, "token_valid": True, "detail": "static-contract"}

    monkeypatch.setattr(probe, "_control_connection", interleaved)
    monkeypatch.setattr(probe, "_abstract_control", abstract_attempt)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            controls = list(pool.map(probe._positive_controls, ("a", "b")))
        assert len(controls) == 2
        assert all(row["after"] - row["before"] == 1 for case in controls for row in case.values())
        groups = list(probe._case_listeners.values())
        assert groups[0]["loopback"].port != groups[1]["loopback"].port
        assert groups[0]["nonloopback"].port != groups[1]["nonloopback"].port
        assert groups[0]["abstract_unix"].name != groups[1]["abstract_unix"].name
    finally:
        for group in probe._case_listeners.values():
            group["loopback"].close()
            group["nonloopback"].close()


@pytest.mark.parametrize("phase", ["targeted", "rollback"])
def test_concurrent_execute_main_and_summary_keep_listener_and_namespace_independence(
    tmp_path, phase
) -> None:
    def rollback_evidence(fixtures):
        for result in fixtures.values():
            result["outcome"] = "failed"
            result["execution"]["returncode"] = 1
            result["execution"]["payload_started"] = False
            del result["payload"]

    probe = _matrix_probe(tmp_path, rollback_evidence if phase == "rollback" else None)
    probe.phase = phase
    observed = {}
    command = probe._command

    def observe_command(case):
        result = command(case)
        observed[case] = probe._case_listeners[case]
        return result

    run = probe._run
    barrier = threading.Barrier(3 if phase == "rollback" else 2)

    def synchronized_run(case, command, work):
        if phase == "rollback" or case != "e6-n0-single":
            barrier.wait(timeout=30)
        return run(case, command, work)

    probe._command = observe_command
    probe._run = synchronized_run
    probe.run()
    summary = probe.summarize()
    if phase == "targeted":
        assert summary["decision"] == "go"
    else:
        assert summary["decision"] == "no-go" and summary["rollback_restored"] is True
    assert len({group["abstract_unix"].name for group in observed.values()}) == 3
    assert len({id(group) for group in observed.values()}) == 3


def test_wrong_parent_control_token_is_infrastructure_failure(monkeypatch) -> None:
    probe = E6Probe.__new__(E6Probe)
    probe._case_listeners = {
        "case": {
            "loopback": SimpleNamespace(host="127.0.0.1", port=1, count=lambda: 0),
            "nonloopback": SimpleNamespace(count=lambda: 0),
            "abstract_unix": SimpleNamespace(count=lambda: 0),
        }
    }
    monkeypatch.setattr(
        probe,
        "_control_connection",
        lambda host, port: {"connected": True, "token_valid": False, "detail": "wrong"},
    )
    with pytest.raises(RuntimeError, match="positive control failed"):
        probe._positive_controls("case")


def test_positive_control_waits_for_listener_count_confirmation() -> None:
    counts = iter((0, 0, 1))
    listener = SimpleNamespace(host="127.0.0.1", port=12345, count=lambda: next(counts))
    controls = positive_controls(
        {"loopback": listener},
        tcp_attempt=lambda host, port: {
            "connected": True,
            "token_valid": True,
            "detail": "connected",
        },
        abstract_attempt=lambda: {},
    )
    assert controls["loopback"]["before"] == 0
    assert controls["loopback"]["after"] == 1


def test_abstract_unix_has_static_contract_and_native_only_dynamic_proof() -> None:
    source = (WORKFLOW.parents[2] / "scripts/e2e/native_sandbox_probe_e6_listeners.py").read_text()
    assert 'connection.connect("\\0" + name)' in source
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "runs-on: ubuntu-24.04" in workflow
    assert "native_sandbox_probe_e6_contract_test.py" in workflow


def test_empty_placeholder_and_journal_errors_are_fail_closed(monkeypatch) -> None:
    journal = audit_module._journal
    monkeypatch.setattr(audit_module, "_journal", lambda *args: "")
    audit = audit_module._case_audit({"started_epoch": 1.0, "ended_epoch": 2.0})
    assert audit["passed"] is False and audit["relevant_count"] == 0
    assert "no matching kernel audit" in audit["rendered"]

    monkeypatch.setattr(audit_module, "_journal", journal)
    for completed in (
        SimpleNamespace(returncode=1, stdout="entries", stderr="failed"),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout="entries", stderr="warning"),
        SimpleNamespace(returncode=0, stdout="-- No entries --\n", stderr=""),
    ):
        monkeypatch.setattr(
            audit_module.subprocess, "run", lambda *args, completed=completed, **kwargs: completed
        )
        with pytest.raises(RuntimeError):
            audit_module._journal("-k")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'operation="capable"' in workflow
    assert 'profile="bwrap//&unpriv_bwrap"' in workflow
    assert 'audit_meta["passed"] is True' in workflow


def _summary_paths(tmp_path: Path) -> tuple[Path, Path]:
    stock = tmp_path / "summary.json"
    candidate = tmp_path / "candidate-summary.json"
    stock.write_text(
        json.dumps(
            {"cases": [{"execution": {"launcher_pid": 1000 + index}} for index in range(15)]}
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            {"cases": [{"execution": {"launcher_pid": 2000 + index}} for index in range(3)]}
        ),
        encoding="utf-8",
    )
    return stock, candidate


def test_quiesce_stop_failure_is_recorded_and_never_restored(monkeypatch, tmp_path) -> None:
    stock, candidate = _summary_paths(tmp_path)
    monkeypatch.setattr(quiesce_module, "_require_root_ci", lambda: None)
    monkeypatch.setattr(
        quiesce_module, "_wait_reclaimed", lambda pids, pgids: {"pids": [], "pgids": []}
    )

    def fake_command(command):
        if command[1] == "stop":
            return {"command": command, "returncode": 1, "stdout": "", "stderr": "stop failed"}
        return {"command": command, "returncode": 3, "stdout": "inactive", "stderr": ""}

    monkeypatch.setattr(quiesce_module, "_command", fake_command)
    evidence = tmp_path / "quiesce.json"
    assert quiesce_module.quiesce(stock, candidate, evidence) is False
    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["quiesced"] is False
    assert len(document["unit_results"]) == 2
    assert all(row["stop"]["returncode"] == 1 for row in document["unit_results"])


def test_quiesce_requires_inactive_units_nonempty_pids_and_reclamation(
    monkeypatch, tmp_path
) -> None:
    stock, candidate = _summary_paths(tmp_path)
    monkeypatch.setattr(quiesce_module, "_require_root_ci", lambda: None)

    def fake_command(command):
        active = command[1] == "is-active"
        return {
            "command": command,
            "returncode": 3 if active else 0,
            "stdout": "inactive" if active else "",
            "stderr": "",
        }

    monkeypatch.setattr(quiesce_module, "_command", fake_command)
    monkeypatch.setattr(
        quiesce_module, "_wait_reclaimed", lambda pids, pgids: {"pids": [], "pgids": []}
    )
    assert quiesce_module.quiesce(stock, candidate, tmp_path / "ok.json") is True
    candidate.write_text(
        json.dumps({"cases": [{"execution": {"launcher_pid": 2000}}]}),
        encoding="utf-8",
    )
    assert quiesce_module.quiesce(stock, candidate, tmp_path / "partial.json") is True
    monkeypatch.setattr(
        quiesce_module, "_wait_reclaimed", lambda pids, pgids: {"pids": [2000], "pgids": []}
    )
    assert quiesce_module.quiesce(stock, candidate, tmp_path / "remaining.json") is False

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"cases": []}), encoding="utf-8")
    assert quiesce_module.quiesce(empty, empty, tmp_path / "empty-quiesce.json") is False
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "|| true" not in workflow
    assert workflow.index("native_sandbox_probe_quiesce.py") < workflow.index(
        'install --repo "$GITHUB_WORKSPACE" || status=$?', workflow.index("R - unload")
    )


def test_corrupt_baseline_still_unloads_managed_but_never_reports_restored(
    monkeypatch, tmp_path
) -> None:
    state = tmp_path / "state.json"
    state.write_text("{truncated", encoding="utf-8")
    managed = tmp_path / "managed"
    managed.write_text("profile", encoding="utf-8")
    calls = []
    monkeypatch.setattr(apparmor, "STATE", state)
    monkeypatch.setattr(apparmor, "MANAGED", managed)
    monkeypatch.setattr(apparmor, "_require_root_ci", lambda: None)
    monkeypatch.setattr(apparmor, "managed_profile_valid", lambda path, expected: True)
    monkeypatch.setattr(apparmor, "read_profiles", lambda path: {"stock": "(enforce)"})
    monkeypatch.setattr(apparmor, "_static_snapshot", lambda: {"Seccomp": "2"})
    monkeypatch.setattr(apparmor, "_run", lambda command: calls.append(command) or {"stdout": "{}"})
    evidence = tmp_path / "rollback.json"
    with pytest.raises(RuntimeError, match="baseline unreadable"):
        apparmor.disable(evidence)
    assert calls[0] == ["/usr/sbin/apparmor_parser", "-R", str(managed)]
    assert not managed.exists()
    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["apparmor_profiles_restored"] is False
    assert document["static_state_restored"] is False
    assert document["managed_absent"] is True


def test_missing_baseline_cannot_claim_restored(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(apparmor, "STATE", tmp_path / "missing-state")
    monkeypatch.setattr(apparmor, "MANAGED", tmp_path / "managed")
    monkeypatch.setattr(apparmor, "_require_root_ci", lambda: None)
    monkeypatch.setattr(
        apparmor, "read_profiles", lambda path: {"stock": "(enforce)", "unexpected": "(enforce)"}
    )
    monkeypatch.setattr(apparmor, "_static_snapshot", lambda: {"Seccomp": "2"})
    monkeypatch.setattr(apparmor, "_run", lambda command: {"stdout": "{}"})
    evidence = tmp_path / "rollback.json"
    with pytest.raises(RuntimeError, match="baseline unreadable"):
        apparmor.disable(evidence)
    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["apparmor_profiles_restored"] is False
    assert document["static_state_restored"] is False


def test_pending_write_uses_atomic_replace_and_workflow_compares_independent_profiles(
    monkeypatch, tmp_path
) -> None:
    from scripts.e2e import native_sandbox_probe_apparmor_state as state_module

    calls = []
    original_replace = state_module.os.replace

    def record_replace(source, target):
        calls.append((source, target))
        original_replace(source, target)

    monkeypatch.setattr(state_module.os, "replace", record_replace)
    destination = tmp_path / "pending.json"
    apparmor._write_evidence(destination, {"before_profiles": {"stock": "(enforce)"}})
    assert (
        len(calls) == 1 and json.loads(destination.read_text(encoding="utf-8"))["before_profiles"]
    )
    assert list(tmp_path.iterdir()) == [destination]
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'before_aa["profiles"] == after_aa["profiles"]' in workflow


def test_absolute_entry_points_import_repo_modules_outside_checkout(tmp_path) -> None:
    root = WORKFLOW.parents[2]
    for name in (
        "native_sandbox_probe_apparmor.py",
        "native_sandbox_probe_collect.py",
        "native_sandbox_probe_quiesce.py",
    ):
        completed = subprocess.run(
            [sys.executable, str(root / "scripts/e2e" / name), "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, (name, completed.stderr)
