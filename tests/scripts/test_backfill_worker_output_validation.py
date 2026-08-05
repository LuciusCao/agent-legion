"""Tests for scripts.backfill_worker_output_validation with the DB layer fully mocked."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import backfill_worker_output_validation as bwv

pytestmark = pytest.mark.no_db


class _FakeReadConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.queries: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> _FakeReadConn:
        self.queries.append((sql, params))
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


def _cm(value: Any):
    @contextlib.contextmanager
    def _ctx(dsn: Any) -> Iterator[Any]:
        yield value

    return _ctx


def _candidate(job_id: str = "job-1", node_key: str = "node-a") -> dict[str, Any]:
    return {"job_id": job_id, "node_key": node_key, "skill_version": "v1"}


def _patch_main_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    verdict: str = "invalid",
    candidates: list[dict[str, Any]] | None = None,
) -> None:
    settings = SimpleNamespace(database_url="dsn", data_dir=tmp_path, root_dir=tmp_path)
    monkeypatch.setattr(bwv, "load_settings", lambda: settings)
    monkeypatch.setattr(bwv, "SkillManager", lambda **kwargs: SimpleNamespace(base_dir=tmp_path))
    monkeypatch.setattr(bwv, "read_connection", _cm(_FakeReadConn([])))
    rows = [_candidate()] if candidates is None else candidates
    monkeypatch.setattr(bwv, "find_candidate_runs", lambda dsn, **kwargs: rows)
    monkeypatch.setattr(bwv, "validate_run", lambda *args, **kwargs: (verdict, "bad output"))


def test_dry_run_reports_failures_without_marking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main_dependencies(monkeypatch, tmp_path)

    def _forbidden_mark(dsn: Any, failures: Any) -> Any:
        raise AssertionError("mark_failed must not run during --dry-run")

    monkeypatch.setattr(bwv, "mark_failed", _forbidden_mark)
    monkeypatch.setattr(sys, "argv", ["prog", "--dry-run"])

    bwv.main()

    out = capsys.readouterr().out
    assert "invalid=1" in out
    assert "dry-run: 1 job(s) would be marked failed" in out


def test_main_marks_failures_when_not_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main_dependencies(monkeypatch, tmp_path)
    marked: list[list[dict[str, Any]]] = []

    def _fake_mark(dsn: Any, failures: list[dict[str, Any]]) -> tuple[int, int]:
        marked.append(failures)
        return (1, len(failures))

    monkeypatch.setattr(bwv, "mark_failed", _fake_mark)
    monkeypatch.setattr(sys, "argv", ["prog"])

    bwv.main()

    assert len(marked) == 1
    assert marked[0][0]["validation_error"] == "bad output"
    assert "marked 1 job(s) failed across 1 node(s)" in capsys.readouterr().out


def test_main_skips_marking_when_nothing_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main_dependencies(monkeypatch, tmp_path, verdict="valid")
    monkeypatch.setattr(
        bwv,
        "mark_failed",
        lambda *args: pytest.fail("mark_failed must not run without failures"),
    )
    monkeypatch.setattr(sys, "argv", ["prog", "--dry-run"])

    bwv.main()

    out = capsys.readouterr().out
    assert "valid=1" in out
    assert "dry-run: 0 job(s) would be marked failed" in out


def test_find_candidate_runs_filters_by_workspace_and_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeReadConn([])
    monkeypatch.setattr(bwv, "read_connection", _cm(conn))

    bwv.find_candidate_runs("dsn", workspace_id="ws-1", since="2026-07-01")

    sql, params = conn.queries[0]
    assert "j.workspace_id = %s" in sql
    assert "r.finished_at >= %s::timestamptz" in sql
    assert params == ["ws-1", "2026-07-01"]


def test_validate_run_reports_missing_skill_as_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    row = _candidate()
    row["manifest_json"] = None
    row["workflow_definition_snapshot_json"] = ""
    monkeypatch.setattr(bwv, "definition_from_job_snapshot", lambda snapshot: None)

    skill_manager = SimpleNamespace(base_dir=tmp_path)
    verdict, message = bwv.validate_run(skill_manager, tmp_path, row, capability_skills={})

    assert verdict == "unknown"
    assert "no skill resolvable" in message


def test_validate_run_marks_invalid_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    row = _candidate()
    row["manifest_json"] = '{"skill": "demo/skill"}'
    row["storage_dir"] = ""
    job_dir = tmp_path / "jobs" / row["job_id"]
    job_dir.mkdir(parents=True)
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    monkeypatch.setattr(bwv, "resolve_workflow_skill", lambda base_dir, skill: skill_dir)
    monkeypatch.setattr(bwv, "run_output_validator", lambda skill, job: "contract violated")

    skill_manager = SimpleNamespace(base_dir=tmp_path)
    verdict, message = bwv.validate_run(skill_manager, tmp_path / "jobs", row, capability_skills={})

    assert verdict == "invalid"
    assert message == "contract violated"


def test_validate_run_treats_missing_input_as_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    row = _candidate()
    row["manifest_json"] = '{"skill": "demo/skill"}'
    row["storage_dir"] = ""
    job_dir = tmp_path / "jobs" / row["job_id"]
    job_dir.mkdir(parents=True)
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    monkeypatch.setattr(bwv, "resolve_workflow_skill", lambda base_dir, skill: skill_dir)
    monkeypatch.setattr(
        bwv, "run_output_validator", lambda skill, job: "Missing input file: x.json"
    )

    skill_manager = SimpleNamespace(base_dir=tmp_path)
    verdict, message = bwv.validate_run(skill_manager, tmp_path / "jobs", row, capability_skills={})

    assert verdict == "unknown"
    assert "Missing input file" in message
