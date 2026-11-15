from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pytest_telemetry

pytestmark = pytest.mark.no_db


def test_rerun_diagnostics_redact_secrets_and_bound_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "reruns.json"
    monkeypatch.setenv("AGENT_LEGION_RERUN_REPORT", str(output))
    session = SimpleNamespace(config=SimpleNamespace())
    pytest_telemetry.pytest_sessionstart(session)
    pytest_telemetry.pytest_runtest_logreport(
        SimpleNamespace(
            outcome="rerun",
            nodeid="tests/test_one.py::test_one",
            when="call",
            longrepr=(
                "postgresql://user:db-password@db.internal/production\n"
                "postgresql+psycopg://user:driver-password@db.internal/production\n"
                "token=super-secret-token Authorization: Bearer bearer-secret\n"
                "Bearer abcdefghijklmnop~+/= and Bearer ~+/=\n"
                "Traceback: controlled failure"
            ),
            sections=[
                ("Captured stdout call", "x" * (pytest_telemetry._SECTION_TEXT_LIMIT + 100)),
                ("Captured custom call", "not collected"),
            ],
        )
    )

    pytest_telemetry.pytest_sessionfinish(session, 0)

    report = json.loads(output.read_text(encoding="utf-8"))["reports"][0]
    assert report["nodeid"] == "tests/test_one.py::test_one"
    assert report["phase"] == "call"
    failure = report["failure"]["text"]
    assert "Traceback: controlled failure" in failure
    assert "db-password" not in failure
    assert "driver-password" not in failure
    assert "db.internal" not in failure
    assert "super-secret-token" not in failure
    assert "bearer-secret" not in failure
    assert "abcdefghijklmnop~+/=" not in failure
    assert "Bearer ~+/=" not in failure
    assert report["sections"] == [
        {
            "name": "Captured stdout call",
            "text": "x" * pytest_telemetry._SECTION_TEXT_LIMIT
            + pytest_telemetry._TRUNCATION_MARKER,
            "truncated": True,
        }
    ]


def test_real_rerun_preserves_first_failure_without_hiding_later_tests(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_controlled_rerun.py"
    test_file.write_text(
        """from pathlib import Path


attempts = 0


def test_controlled_rerun():
    global attempts
    attempts += 1
    if attempts == 1:
        print("captured postgresql://user:captured-db-password@db.internal/production")
        print("postgresql+asyncpg://user:driver-password@db.internal/production")
        print("Bearer abcdefghijklmnop~+/= and Bearer ~+/=")
        raise RuntimeError(
            "controlled first failure; "
            "postgresql://user:traceback-db-password@db.internal/production; "
            "postgresql+psycopg://user:traceback-driver-password@db.internal/production; "
            "token=traceback-secret-token"
        )


def test_after_rerun_still_runs(tmp_path):
    (tmp_path / "later-test-ran").write_text("yes", encoding="utf-8")
""",
        encoding="utf-8",
    )
    output = tmp_path / "reruns.json"
    env = {
        **os.environ,
        "AGENT_LEGION_RERUN_REPORT": str(output),
        "PYTHONPATH": str(Path(pytest_telemetry.__file__).resolve().parent.parent),
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp",
            str(tmp_path / "pytest-tmp"),
            "--reruns",
            "1",
            "-p",
            "scripts.pytest_telemetry",
            str(test_file),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
    assert (tmp_path / "pytest-tmp" / "test_after_rerun_still_runs0" / "later-test-ran").is_file()
    raw_payload = output.read_text(encoding="utf-8")
    assert "captured-db-password" not in raw_payload
    assert "traceback-db-password" not in raw_payload
    assert "driver-password" not in raw_payload
    assert "traceback-driver-password" not in raw_payload
    assert "traceback-secret-token" not in raw_payload
    assert "db.internal" not in raw_payload
    assert "abcdefghijklmnop~+/=" not in raw_payload
    assert "Bearer ~+/=" not in raw_payload
    payload = json.loads(raw_payload)
    assert payload["exitstatus"] == 0
    assert payload["tests"] == ["test_controlled_rerun.py::test_controlled_rerun"]
    report = payload["reports"][0]
    assert report["phase"] == "call"
    assert "RuntimeError: controlled first failure" in report["failure"]["text"]
    assert "def test_controlled_rerun" in report["failure"]["text"]
    assert report["failure"]["truncated"] is False
    sections = {section["name"]: section["text"] for section in report["sections"]}
    assert "<redacted-dsn>" in sections["Captured stdout call"]
