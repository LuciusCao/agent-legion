# ruff: noqa: E402
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# The tool package lives inside a hyphenated directory, so add its parent to
# the import path for the tests.
_TOOL_DIR = Path(__file__).parents[2] / "tools" / "comprehension-uploader"
sys.path.insert(0, str(_TOOL_DIR))

from comprehension_uploader import cli
from comprehension_uploader.config import Config
from comprehension_uploader.db import Database
from comprehension_uploader.fingerprint import compute_question_fingerprint
from comprehension_uploader.package_parser import PackageParseError, UploadRecord, parse_package
from comprehension_uploader.question_source import JSONFileQuestionSource
from comprehension_uploader.scanner import Scanner
from comprehension_uploader.uploader import Uploader

from server.app.workflows.question_fingerprint import (
    compute_question_fingerprint as server_compute_question_fingerprint,
)


def _make_config(tmp_path: Path, upload_on_duplicate: str = "update") -> Config:
    return Config(
        api_base_url="http://example.com",
        auth_token_env="COMPREHENSION_API_TOKEN",
        db_path=str(tmp_path / "test.db"),
        question_source={"type": "json_file", "path": str(tmp_path / "questions.json")},
        upload_on_duplicate=upload_on_duplicate,  # type: ignore[arg-type]
        request_timeout=5,
        max_retries=0,
    )


def _make_record(
    question_id: str = "Q100",
    stem: str = "What is 2+2?",
    options: list[dict[str, str]] | None = None,
    comprehension_data: Any = None,
    difficulty: int = 50,
    format_vno: str = "v1",
) -> UploadRecord:
    if options is None:
        options = [{"label": "A", "text": "3"}, {"label": "B", "text": "4"}]
    if comprehension_data is None:
        comprehension_data = {"steps": [{"text": "step1"}]}
    return UploadRecord(
        question_id=question_id,
        subject_id=2,
        question_uuid="uuid-1",
        question_vno=1,
        comprehension_difficulty=difficulty,
        format_vno=format_vno,
        comprehension_data=comprehension_data,
        stem=stem,
        options=options,
    )


class _FakeAPIClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def add(self, record: UploadRecord, fingerprint: str) -> dict[str, Any]:
        self.calls.append(("add", fingerprint, None))
        return self.responses.pop(0)

    def update(
        self, record: UploadRecord, fingerprint: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("update", fingerprint, fields))
        return self.responses.pop(0)


def test_fingerprint_matches_existing_algorithm() -> None:
    stem = "  The   Earth  is round  "
    options = [
        {"label": "B", "text": " False ", "extra": 1},
        {"label": "A", "text": " True"},
    ]
    assert compute_question_fingerprint(stem, options) == server_compute_question_fingerprint(
        stem, options
    )


def test_fingerprint_trusts_provided_value_when_components_missing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "Q101",
                "fingerprint": "deadbeef",
                "comprehension_data": {"x": 1},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record = next(parse_package(package))
    assert record.fingerprint == "deadbeef"

    config = _make_config(tmp_path)
    db = Database(config.db_path)
    db.init_schema()
    uploader = Uploader(config, db, _FakeAPIClient([{"code": 0, "data": {"result": 7}}]))
    with caplog.at_level("WARNING"):
        uploader.upload_one(record, "batch-1")
    assert "trusting provided fingerprint" in caplog.text

    state = db.states.get("Q101")
    assert state is not None
    assert state["latest_fingerprint"] == "deadbeef"


def test_package_parser_rejects_invalid_jsonl(tmp_path: Path) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text('{"question_id": "Q1"}\nnot valid json\n', encoding="utf-8")
    with pytest.raises(PackageParseError):
        list(parse_package(package))


def test_package_parser_stringifies_comprehension_data(tmp_path: Path) -> None:
    package = tmp_path / "package.jsonl"
    payload = {
        "question_id": "Q1",
        "stem": "s",
        "options": [{"label": "A", "text": "a"}],
        "comprehension_data": [{"step": 1}, {"step": 2}],
    }
    package.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    record = next(parse_package(package))
    assert isinstance(record.comprehension_data, str)
    parsed = json.loads(record.comprehension_data)
    assert parsed == [{"step": 1}, {"step": 2}]


def test_uploader_add_success_and_duplicate_update(tmp_path: Path) -> None:
    config = _make_config(tmp_path, upload_on_duplicate="update")
    db = Database(config.db_path)
    db.init_schema()

    record1 = _make_record(question_id="Q100", difficulty=50, format_vno="v1")
    record2 = _make_record(
        question_id="Q100",
        difficulty=60,
        format_vno="v2",
        comprehension_data={"steps": [{"text": "updated"}]},
    )

    client = _FakeAPIClient(
        [
            {"code": 0, "message": "ok", "data": {"result": 42}},
            {"code": 11051, "message": "duplicate"},
            {"code": 0, "message": "updated"},
        ]
    )
    uploader = Uploader(config, db, client)
    uploader.upload_batch([record1, record2], "batch-1")

    logs = db.logs.get_logs("Q100")
    assert len(logs) == 2
    assert logs[1]["action"] == "add"
    assert logs[1]["status"] == "success"
    assert logs[1]["uploaded_record_id"] == 42
    assert logs[0]["action"] == "update"
    assert logs[0]["status"] == "success"

    state = db.states.get("Q100")
    assert state is not None
    assert state["latest_upload_log_id"] == logs[0]["id"]
    assert client.calls[2][0] == "update"
    update_fields = client.calls[2][2]
    assert update_fields["comprehension_difficulty"] == 60
    assert update_fields["format_vno"] == "v2"
    assert "comprehension_data" in update_fields


def test_uploader_add_success_and_duplicate_skip(tmp_path: Path) -> None:
    config = _make_config(tmp_path, upload_on_duplicate="skip")
    db = Database(config.db_path)
    db.init_schema()

    record1 = _make_record(question_id="Q200")
    record2 = _make_record(question_id="Q200")

    client = _FakeAPIClient(
        [
            {"code": 0, "message": "ok", "data": {"result": 1}},
            {"code": 11051, "message": "duplicate"},
        ]
    )
    uploader = Uploader(config, db, client)
    uploader.upload_batch([record1, record2], "batch-2")

    logs = db.logs.get_logs("Q200")
    assert logs[0]["action"] == "skip"
    assert logs[0]["status"] == "skipped"
    assert logs[0]["api_code"] == 11051
    assert not any(call[0] == "update" for call in client.calls)


def test_scan_detects_fingerprint_change(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    db = Database(config.db_path)
    db.init_schema()

    record = _make_record(
        question_id="Q300", stem="Original stem", options=[{"label": "A", "text": "old"}]
    )
    client = _FakeAPIClient([{"code": 0, "message": "ok", "data": {"result": 1}}])
    uploader = Uploader(config, db, client)
    uploader.upload_one(record, "batch-3")

    snapshot_path = tmp_path / "questions.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "Q300": {
                    "stem": "Changed stem",
                    "options": [{"label": "A", "text": "new"}],
                }
            }
        ),
        encoding="utf-8",
    )
    source = JSONFileQuestionSource(snapshot_path)
    scanner = Scanner(config, db, source)

    output_path = tmp_path / "stale.json"
    summary = scanner.scan(str(output_path))

    assert summary["total"] == 1
    assert summary["stale"] == 1
    assert summary["failed"] == 0
    stale = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(stale) == 1
    assert stale[0]["question_id"] == "Q300"
    assert stale[0]["old_fingerprint"] != stale[0]["new_fingerprint"]

    state = db.states.get("Q300")
    assert state is not None
    assert state["stale_reason"] == "fingerprint_changed"


def test_db_schema_and_indexes(tmp_path: Path) -> None:
    db = Database(tmp_path / "schema.db")
    db.init_schema()
    rows = db._conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index')"
    ).fetchall()
    names = {row["name"] for row in rows}
    assert "upload_logs" in names
    assert "question_state" in names
    assert "scan_results" in names
    assert "idx_logs_question" in names
    assert "idx_logs_fingerprint" in names
    assert "idx_logs_batch" in names
    assert "idx_logs_workspace" in names
    assert "idx_scan_question" in names


def _make_cli_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "api_base_url: http://example.com",
                "auth_token_env: COMPREHENSION_API_TOKEN",
                f"db_path: {tmp_path / 'cli.db'}",
                f'question_source: {{"type": "json_file", "path": "{tmp_path / "questions.json"}"}}',
                "upload_on_duplicate: skip",
                "request_timeout: 5",
                "max_retries: 0",
            ]
        ),
        encoding="utf-8",
    )
    return config


def _make_cli_package(tmp_path: Path) -> Path:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "QCLI",
                "subject_id": 2,
                "question_uuid": "uuid-cli",
                "question_vno": 1,
                "comprehension_difficulty": 50,
                "format_vno": "v1",
                "comprehension_data": {"steps": []},
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return package


def test_cli_upload_uses_workspace_as_batch_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _make_cli_config(tmp_path)
    package_path = _make_cli_package(tmp_path)

    captured: dict[str, Any] = {}

    def fake_upload_batch(
        self: Uploader, records: list[Any], batch_id: str, workspace_id: str | None = None
    ) -> None:
        captured["batch_id"] = batch_id
        captured["workspace_id"] = workspace_id
        captured["count"] = len(records)

    monkeypatch.setattr(Uploader, "upload_batch", fake_upload_batch)
    monkeypatch.setenv("COMPREHENSION_API_TOKEN", "token")

    rc = cli.main(
        [
            "upload",
            "--config",
            str(config_path),
            "--workspace",
            "ws-123",
            str(package_path),
        ]
    )
    assert rc == 0
    assert captured["batch_id"].startswith("ws-123-")
    assert captured["workspace_id"] == "ws-123"
    assert captured["count"] == 1
    assert "ws-123" in capsys.readouterr().out


def test_cli_upload_batch_id_overrides_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _make_cli_config(tmp_path)
    package_path = _make_cli_package(tmp_path)

    captured: dict[str, Any] = {}

    def fake_upload_batch(
        self: Uploader, records: list[Any], batch_id: str, workspace_id: str | None = None
    ) -> None:
        captured["batch_id"] = batch_id
        captured["workspace_id"] = workspace_id

    monkeypatch.setattr(Uploader, "upload_batch", fake_upload_batch)
    monkeypatch.setenv("COMPREHENSION_API_TOKEN", "token")

    rc = cli.main(
        [
            "upload",
            "--config",
            str(config_path),
            "--workspace",
            "ws-123",
            "--batch-id",
            "batch-999",
            str(package_path),
        ]
    )
    assert rc == 0
    assert captured["batch_id"] == "batch-999"
    assert captured["workspace_id"] == "ws-123"
