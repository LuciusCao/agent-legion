# ruff: noqa: E402
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# The tool package lives inside a hyphenated directory, so add its parent to
# the import path for the tests.
_TOOL_DIR = Path(__file__).parents[2] / "tools" / "comprehension-uploader"
sys.path.insert(0, str(_TOOL_DIR))

from comprehension_uploader import cli
from comprehension_uploader.auth import AuthError, get_token
from comprehension_uploader.config import Config
from comprehension_uploader.db import Database
from comprehension_uploader.fingerprint import compute_question_fingerprint
from comprehension_uploader.package_parser import (
    PackageParseError,
    UploadRecord,
    parse_package,
    validate_package,
)
from comprehension_uploader.packager import package_comprehension_info_from_workspace_zip
from comprehension_uploader.question_source import JSONFileQuestionSource
from comprehension_uploader.scanner import Scanner
from comprehension_uploader.uploader import Uploader

from server.app.workflows.question_fingerprint import (
    compute_question_fingerprint as server_compute_question_fingerprint,
)


def _make_config(tmp_path: Path, upload_on_duplicate: str = "update") -> Config:
    return Config(
        api_base_url="http://example.com",
        db_path=str(tmp_path / "test.db"),
        question_source={"type": "json_file", "path": str(tmp_path / "questions.json")},
        upload_on_duplicate=upload_on_duplicate,  # type: ignore[arg-type]
        request_timeout=5,
        max_retries=0,
    )


def _make_valid_comprehension_data(difficulty: int = 50) -> dict[str, Any]:
    return {
        "fingerprint": "fp-1",
        "comprehension_difficulty": difficulty,
        "key_info_list": [
            {
                "key_info_id": "ki_001",
                "type": "given",
                "content": {
                    "text": "题干中的关键信息",
                    "position": {"start": 0, "end": 5},
                },
                "question": {
                    "text": "关键问题是什么？",
                    "options": [{"label": "A", "text": "正确选项", "is_correct": True}],
                },
                "question_comprehension_ability": "information_locating",
            }
        ],
        "possible_error_list": [
            {
                "error_id": "pe_001",
                "error_type": "question_comprehension",
                "position": 1,
                "error_answer": ["错误答案"],
                "error_description": "学生可能误解题意。",
                "cognitive_basis": "学生尚未掌握相关概念。",
                "related_key_info_ids": ["ki_001"],
            }
        ],
    }


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
        comprehension_data = _make_valid_comprehension_data(difficulty)
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
                "comprehension_data": _make_valid_comprehension_data(),
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
        "comprehension_data": _make_valid_comprehension_data(),
    }
    package.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    record = next(parse_package(package))
    assert isinstance(record.comprehension_data, str)
    parsed = json.loads(record.comprehension_data)
    assert parsed == payload["comprehension_data"]


def test_uploader_add_success_and_duplicate_update(tmp_path: Path) -> None:
    config = _make_config(tmp_path, upload_on_duplicate="update")
    db = Database(config.db_path)
    db.init_schema()

    record1 = _make_record(question_id="Q100", difficulty=50, format_vno="v1")
    record2 = _make_record(
        question_id="Q100",
        difficulty=60,
        format_vno="v1",
        comprehension_data=_make_valid_comprehension_data(60),
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
    assert "comprehension_data" in update_fields


def test_uploader_duplicate_update_includes_format_vno_when_changed(
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path, upload_on_duplicate="update")
    db = Database(config.db_path)
    db.init_schema()

    record1 = UploadRecord(
        question_id="Q100",
        subject_id=2,
        question_uuid="uuid-1",
        question_vno=1,
        comprehension_difficulty=50,
        format_vno=None,
        comprehension_data=_make_valid_comprehension_data(50),
        stem="What is 2+2?",
        options=[{"label": "A", "text": "3"}, {"label": "B", "text": "4"}],
    )
    record2 = _make_record(
        question_id="Q100",
        difficulty=50,
        format_vno="v1",
        comprehension_data=_make_valid_comprehension_data(50),
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

    update_fields = client.calls[2][2]
    assert update_fields["format_vno"] == "v1"


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


def test_uploader_skips_non_uploadable_records(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    db = Database(config.db_path)
    db.init_schema()

    package = tmp_path / "package.jsonl"
    package.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "question_id": "Q-uploadable",
                        "subject_id": 2,
                        "question_uuid": "uuid-1",
                        "question_vno": 1,
                        "comprehension_difficulty": 50,
                        "format_vno": "v1",
                        "comprehension_data": {
                            "fingerprint": "fp1",
                            "comprehension_difficulty": 50,
                            "key_info_list": [],
                            "possible_error_list": [],
                        },
                        "stem": "uploadable stem",
                        "options": [{"label": "A", "text": "a"}],
                    }
                ),
                json.dumps(
                    {
                        "question_id": "Q-uploadable-false",
                        "uploadable": False,
                        "comprehension_data": {
                            "fingerprint": "fp2",
                            "comprehension_difficulty": 50,
                            "key_info_list": [],
                            "possible_error_list": [],
                        },
                        "stem": "skipped stem",
                        "options": [{"label": "A", "text": "a"}],
                    }
                ),
                json.dumps(
                    {
                        "question_id": "Q-non-uploadable-outcome",
                        "outcome": "non_uploadable",
                        "comprehension_data": {
                            "fingerprint": "fp3",
                            "comprehension_difficulty": 50,
                            "key_info_list": [],
                            "possible_error_list": [],
                        },
                        "stem": "skipped stem 2",
                        "options": [{"label": "A", "text": "a"}],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = list(parse_package(package))
    client = _FakeAPIClient([{"code": 0, "message": "ok", "data": {"result": 1}}])
    uploader = Uploader(config, db, client)
    uploader.upload_batch(records, "batch-skip")

    assert len(client.calls) == 1
    assert client.calls[0] == (
        "add",
        compute_question_fingerprint("uploadable stem", [{"label": "A", "text": "a"}]),
        None,
    )
    assert db.logs.get_logs("Q-uploadable-false") == []
    assert db.logs.get_logs("Q-non-uploadable-outcome") == []


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
                "comprehension_data": _make_valid_comprehension_data(),
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
    monkeypatch.setenv("BASECMS_TOKEN", "token")

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
    monkeypatch.setenv("BASECMS_TOKEN", "token")

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


def test_get_token_prefers_basecms_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASECMS_TOKEN", "direct-token")
    monkeypatch.delenv("BASECMS_APP_ID", raising=False)
    assert get_token({}) == "direct-token"


def test_get_token_generates_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASECMS_TOKEN", raising=False)
    monkeypatch.setenv("BASECMS_APP_ID", "app-1")
    monkeypatch.setenv("BASECMS_NONCE", "nonce-1")
    monkeypatch.setenv("BASECMS_SECRET", "secret-1")
    monkeypatch.setenv("BASECMS_TOKEN_URL", "http://auth.example.com/token")

    with patch("comprehension_uploader.auth.requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"data": {"token": "generated-token"}}
        mock_post.return_value.raise_for_status = lambda: None
        token = get_token({})

    assert token == "generated-token"
    call_kwargs = mock_post.call_args.kwargs
    payload = call_kwargs["json"]
    assert payload["app_id"] == "app-1"
    assert payload["nonce"] == "nonce-1"
    assert payload["secret"] == "secret-1"
    assert "timestamp" in payload
    assert "sign" in payload
    assert call_kwargs["timeout"] == 10


def test_get_token_raises_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASECMS_TOKEN", raising=False)
    monkeypatch.delenv("BASECMS_APP_ID", raising=False)
    monkeypatch.delenv("BASECMS_NONCE", raising=False)
    monkeypatch.delenv("BASECMS_SECRET", raising=False)
    monkeypatch.delenv("BASECMS_TOKEN_URL", raising=False)
    monkeypatch.setattr("comprehension_uploader.auth._maybe_load_dotenv", lambda: None)
    with pytest.raises(AuthError):
        get_token({})


# ---------------------------------------------------------------------------
# Schema validation and format_vno resolution
# ---------------------------------------------------------------------------


def test_package_parser_accepts_valid_v1_comprehension_data(tmp_path: Path) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "Q1",
                "format_vno": "v1",
                "comprehension_data": _make_valid_comprehension_data(),
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record = next(parse_package(package))
    assert record.format_vno == "v1"
    assert record.question_id == "Q1"


def test_validate_package_rejects_invalid_v1_comprehension_data(tmp_path: Path) -> None:
    package = tmp_path / "package.jsonl"
    invalid_data = _make_valid_comprehension_data()
    invalid_data["possible_error_list"][0].pop("cognitive_basis")
    package.write_text(
        json.dumps(
            {
                "question_id": "Q1",
                "format_vno": "v1",
                "comprehension_data": invalid_data,
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    passed, failed, errors = validate_package(package)
    assert passed == 0
    assert failed == 1
    assert "v1" in errors[0]
    assert "schema validation failed" in errors[0].lower()


def test_validate_package_unsupported_version(tmp_path: Path) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "Q1",
                "format_vno": "v99",
                "comprehension_data": _make_valid_comprehension_data(),
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    passed, failed, errors = validate_package(package)
    assert passed == 0
    assert failed == 1
    assert "v99" in errors[0]
    assert "unsupported" in errors[0].lower()


def test_parse_package_does_not_validate_schema(tmp_path: Path) -> None:
    package = tmp_path / "package.jsonl"
    invalid_data = _make_valid_comprehension_data()
    invalid_data["possible_error_list"][0].pop("cognitive_basis")
    package.write_text(
        json.dumps(
            {
                "question_id": "Q1",
                "format_vno": "v1",
                "comprehension_data": invalid_data,
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    records = list(parse_package(package))
    assert len(records) == 1
    assert records[0].question_id == "Q1"


def test_package_parser_format_vno_fallback_to_v1(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "Q1",
                "comprehension_data": _make_valid_comprehension_data(),
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        record = next(parse_package(package))
    assert record.format_vno == "v1"
    assert "defaulting to v1" in caplog.text


def test_package_parser_format_vno_from_comprehension_info_schema_version(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "Q1",
                "comprehension_info_schema_version": "v1",
                "comprehension_data": _make_valid_comprehension_data(),
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record = next(parse_package(package))
    assert record.format_vno == "v1"


def test_package_parser_format_vno_normalization(tmp_path: Path) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "Q1",
                "format_vno": "  v1  ",
                "comprehension_data": _make_valid_comprehension_data(),
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record = next(parse_package(package))
    assert record.format_vno == "v1"


def test_uploader_logs_api_code_10011_on_validation_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _make_config(tmp_path)
    db = Database(config.db_path)
    db.init_schema()

    record = _make_record(question_id="Q400")
    # Corrupt the already-serialized comprehension_data so it fails the
    # defensive validation inside upload_one.
    record.comprehension_data = json.dumps({"invalid": "data"})

    uploader = Uploader(config, db, _FakeAPIClient([]))
    with caplog.at_level("WARNING"):
        uploader.upload_one(record, "batch-4")

    logs = db.logs.get_logs("Q400")
    assert len(logs) == 1
    assert logs[0]["action"] == "validate"
    assert logs[0]["status"] == "failed"
    assert logs[0]["api_code"] == 10011
    assert logs[0]["format_vno"] == "v1"


# ---------------------------------------------------------------------------
# Package and validate CLI commands
# ---------------------------------------------------------------------------


def test_cli_package_command_builds_jsonl(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    question_dir = input_dir / "QPKG"
    question_dir.mkdir()
    question_dir.joinpath("comprehension_info.json").write_text(
        json.dumps(
            {
                "question_id": "QPKG",
                "schema_version": "v1",
                "comprehension_data": _make_valid_comprehension_data(55),
            }
        ),
        encoding="utf-8",
    )

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "QPKG": {
                    "question_id": "QPKG",
                    "subject_id": 3,
                    "question_uuid": "uuid-pkg",
                    "question_vno": 2,
                    "stem": "Package stem",
                    "options": [{"label": "A", "text": "pkg-a"}],
                }
            }
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "api_base_url: http://example.com",
                f"db_path: {tmp_path / 'pkg.db'}",
                f'question_source: {{"type": "json_file", "path": "{questions_path}"}}',
                "upload_on_duplicate: skip",
                "request_timeout: 5",
                "max_retries: 0",
            ]
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "package.jsonl"
    rc = cli.main(
        [
            "package",
            "--config",
            str(config_path),
            "--input-dir",
            str(input_dir),
            "--output",
            str(output_path),
        ]
    )
    assert rc == 0
    lines = output_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    package_line = json.loads(lines[0])
    assert package_line["question_id"] == "QPKG"
    assert package_line["subject_id"] == 3
    assert package_line["question_uuid"] == "uuid-pkg"
    assert package_line["question_vno"] == 2
    assert package_line["comprehension_difficulty"] == 55
    assert package_line["format_vno"] == "v1"
    assert package_line["stem"] == "Package stem"
    assert package_line["options"] == [{"label": "A", "text": "pkg-a"}]
    assert "written" in capsys.readouterr().out


def test_cli_validate_command_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "QV1",
                "format_vno": "v1",
                "comprehension_data": _make_valid_comprehension_data(),
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = cli.main(["validate", str(package)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "passed=1" in out
    assert "failed=0" in out


def test_cli_validate_command_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "package.jsonl"
    invalid_data = _make_valid_comprehension_data()
    invalid_data["comprehension_difficulty"] = 0
    package.write_text(
        json.dumps(
            {
                "question_id": "QV2",
                "format_vno": "v1",
                "comprehension_data": invalid_data,
                "stem": "s",
                "options": [{"label": "A", "text": "a"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = cli.main(["validate", str(package)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "passed=0" in captured.out
    assert "failed=1" in captured.out
    assert "v1" in captured.err


def test_package_from_workspace_zip_builds_jsonl(tmp_path: Path) -> None:
    import zipfile

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "QZIP": {
                    "question_id": "QZIP",
                    "subject_id": 3,
                    "question_uuid": "uuid-zip",
                    "question_vno": 2,
                    "stem": "Zip stem",
                    "options": [{"label": "A", "text": "zip-a"}],
                }
            }
        ),
        encoding="utf-8",
    )

    workspace_zip = tmp_path / "workspace-jobs-20260706120000000000.zip"
    with zipfile.ZipFile(workspace_zip, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"jobs": [{"id": "job-1"}]}))
        zf.writestr(
            "job-1/comprehension_info.json",
            json.dumps(
                {
                    "question_id": "QZIP",
                    "schema_version": "v1",
                    "comprehension_data": _make_valid_comprehension_data(55),
                }
            ),
        )

    output_path = tmp_path / "package.jsonl"
    summary = package_comprehension_info_from_workspace_zip(
        workspace_zip, output_path, JSONFileQuestionSource(questions_path)
    )
    assert summary["written"] == 1
    assert summary["skipped"] == 0

    lines = output_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    package_line = json.loads(lines[0])
    assert package_line["question_id"] == "QZIP"
    assert package_line["format_vno"] == "v1"
    assert package_line["stem"] == "Zip stem"


def _make_workspace_zip(tmp_path: Path, question_id: str, file_name: str = "workspace.zip") -> Path:
    import zipfile

    workspace_zip = tmp_path / file_name
    with zipfile.ZipFile(workspace_zip, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"jobs": [{"id": "job-1"}]}))
        zf.writestr(
            "job-1/comprehension_info.json",
            json.dumps(
                {
                    "question_id": question_id,
                    "schema_version": "v1",
                    "comprehension_data": _make_valid_comprehension_data(55),
                }
            ),
        )
    return workspace_zip


def test_cli_package_command_from_workspace_zip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace_zip = _make_workspace_zip(tmp_path, "QWS")

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "QWS": {
                    "question_id": "QWS",
                    "subject_id": 3,
                    "question_uuid": "uuid-ws",
                    "question_vno": 2,
                    "stem": "Workspace stem",
                    "options": [{"label": "A", "text": "ws-a"}],
                }
            }
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "api_base_url: http://example.com",
                f"db_path: {tmp_path / 'ws_pkg.db'}",
                f'question_source: {{"type": "json_file", "path": "{questions_path}"}}',
                "upload_on_duplicate: skip",
                "request_timeout: 5",
                "max_retries: 0",
            ]
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out.jsonl"
    rc = cli.main(
        [
            "package",
            "--config",
            str(config_path),
            "--workspace-package",
            str(workspace_zip),
            "--output",
            str(output_path),
        ]
    )
    assert rc == 0
    lines = output_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    package_line = json.loads(lines[0])
    assert package_line["question_id"] == "QWS"
    assert package_line["stem"] == "Workspace stem"


def test_cli_upload_command_from_workspace_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_zip = _make_workspace_zip(tmp_path, "QUP")

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "QUP": {
                    "question_id": "QUP",
                    "subject_id": 3,
                    "question_uuid": "uuid-up",
                    "question_vno": 2,
                    "stem": "Upload stem",
                    "options": [{"label": "A", "text": "up-a"}],
                }
            }
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "api_base_url: http://example.com",
                f"db_path: {tmp_path / 'ws_up.db'}",
                f'question_source: {{"type": "json_file", "path": "{questions_path}"}}',
                "upload_on_duplicate: skip",
                "request_timeout: 5",
                "max_retries: 0",
            ]
        ),
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    def fake_upload_batch(
        self: Uploader, records: list[Any], batch_id: str, workspace_id: str | None = None
    ) -> None:
        captured["count"] = len(records)
        captured["workspace_id"] = workspace_id

    monkeypatch.setattr(Uploader, "upload_batch", fake_upload_batch)
    monkeypatch.setenv("BASECMS_TOKEN", "token")

    rc = cli.main(
        [
            "upload",
            "--config",
            str(config_path),
            "--workspace",
            "ws-zip",
            "--workspace-package",
            str(workspace_zip),
        ]
    )
    assert rc == 0
    assert captured["count"] == 1
    assert captured["workspace_id"] == "ws-zip"
