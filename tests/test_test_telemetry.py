from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts import pytest_telemetry
from scripts.summarize_test_results import parse_junit, parse_reruns, render_summary


def test_pytest_telemetry_records_controller_reruns(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "reruns.json"
    monkeypatch.setenv("AGENT_LEGION_RERUN_REPORT", str(output))
    session = SimpleNamespace(config=SimpleNamespace())
    pytest_telemetry.pytest_sessionstart(session)
    pytest_telemetry.pytest_runtest_logreport(
        SimpleNamespace(outcome="rerun", nodeid="tests/test_one.py::test_one", when="call")
    )
    pytest_telemetry.pytest_runtest_logreport(
        SimpleNamespace(outcome="passed", nodeid="tests/test_two.py::test_two", when="call")
    )

    pytest_telemetry.pytest_sessionfinish(session, 0)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["attempts"] == 1
    assert payload["tests"] == ["tests/test_one.py::test_one"]
    assert payload["exitstatus"] == 0


def test_pytest_telemetry_does_not_write_from_xdist_worker(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "reruns.json"
    monkeypatch.setenv("AGENT_LEGION_RERUN_REPORT", str(output))
    session = SimpleNamespace(config=SimpleNamespace(workerinput={"workerid": "gw0"}))

    pytest_telemetry.pytest_sessionfinish(session, 0)

    assert not output.exists()


def test_summary_parses_junit_and_rerun_reports(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="sample">
    <testcase name="passed" time="0.2" />
    <testcase name="failed" time="0.3"><failure>boom</failure></testcase>
    <testcase name="skipped" time="0.1"><skipped /></testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
    rerun_path = tmp_path / "reruns.json"
    rerun_path.write_text(
        json.dumps({"attempts": 2, "tests": ["tests/test_one.py::test_one"]}),
        encoding="utf-8",
    )

    junit = parse_junit(junit_path)
    attempts, rerun_test_count = parse_reruns([rerun_path])
    rendered = render_summary(
        title="Test evidence",
        junit=[junit],
        rerun_attempts=attempts,
        rerun_test_count=rerun_test_count,
        missing=[],
    )

    assert junit.tests == 3
    assert junit.passed == 1
    assert junit.failures == 1
    assert junit.skipped == 1
    assert junit.duration_seconds == 0.6
    assert "Rerun attempts: **2** across **1** tests." in rendered


def test_summary_cli_tolerates_malformed_optional_evidence(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    malformed = tmp_path / "junit.xml"
    malformed.write_text("<testsuite>", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "summarize_test_results.py",
            "--title",
            "Incomplete evidence",
            "--junit",
            str(malformed),
        ],
    )

    from scripts.summarize_test_results import main

    assert main() == 0
    output = capsys.readouterr().out
    assert "No JUnit reports were available." in output
    assert "Missing optional evidence files: **1**." in output
