# ruff: noqa: E402
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# The tool package lives inside a hyphenated directory, so add its parent to
# the import path for the tests.
_TOOL_DIR = Path(__file__).parents[2] / "tools" / "comprehension-uploader"
sys.path.insert(0, str(_TOOL_DIR))

from comprehension_uploader.fingerprint import compute_question_fingerprint
from comprehension_uploader.package_parser import (
    PackageParseError,
    UploadRecord,
    parse_package,
    validate_package,
)
from comprehension_uploader.question_source import JSONFileQuestionSource
from comprehension_uploader.scanner import Scanner
from comprehension_uploader.uploader import Uploader

from tests.tools.comprehension_uploader_fixtures import (
    FakeAPIClient,
    make_config,
    make_record,
    make_valid_comprehension_data,
)
from workspace_libs.question_fingerprint import (
    compute_question_fingerprint as canonical_compute_question_fingerprint,
)


def test_fingerprint_matches_existing_algorithm() -> None:
    stem = "  The   Earth  is round  "
    options = [
        {"label": "B", "text": " False ", "extra": 1},
        {"label": "A", "text": " True"},
    ]
    assert compute_question_fingerprint(stem, options) == canonical_compute_question_fingerprint(
        stem, options
    )


def test_fingerprint_trusts_provided_value_when_components_missing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    database,
) -> None:
    package = tmp_path / "package.jsonl"
    package.write_text(
        json.dumps(
            {
                "question_id": "Q101",
                "fingerprint": "deadbeef",
                "comprehension_data": make_valid_comprehension_data(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record = next(parse_package(package))
    assert record.fingerprint == "deadbeef"

    config = make_config(tmp_path)
    db = database(config.db_path)
    db.init_schema()
    uploader = Uploader(config, db, FakeAPIClient([{"code": 0, "data": {"result": 7}}]))
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
        "comprehension_data": make_valid_comprehension_data(),
    }
    package.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    record = next(parse_package(package))
    assert isinstance(record.comprehension_data, str)
    parsed = json.loads(record.comprehension_data)
    assert parsed == payload["comprehension_data"]


def test_uploader_add_success_and_duplicate_update(tmp_path: Path, database) -> None:
    config = make_config(tmp_path, upload_on_duplicate="update")
    db = database(config.db_path)
    db.init_schema()

    record1 = make_record(question_id="Q100", difficulty=50, format_vno="v1")
    record2 = make_record(
        question_id="Q100",
        difficulty=60,
        format_vno="v1",
        comprehension_data=make_valid_comprehension_data(60),
    )

    client = FakeAPIClient(
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
    database,
) -> None:
    config = make_config(tmp_path, upload_on_duplicate="update")
    db = database(config.db_path)
    db.init_schema()

    record1 = UploadRecord(
        question_id="Q100",
        subject_id=2,
        question_uuid="uuid-1",
        question_vno=1,
        comprehension_difficulty=50,
        format_vno=None,
        comprehension_data=make_valid_comprehension_data(50),
        stem="What is 2+2?",
        options=[{"label": "A", "text": "3"}, {"label": "B", "text": "4"}],
    )
    record2 = make_record(
        question_id="Q100",
        difficulty=50,
        format_vno="v1",
        comprehension_data=make_valid_comprehension_data(50),
    )

    client = FakeAPIClient(
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


def test_uploader_add_success_and_duplicate_skip(tmp_path: Path, database) -> None:
    config = make_config(tmp_path, upload_on_duplicate="skip")
    db = database(config.db_path)
    db.init_schema()

    record1 = make_record(question_id="Q200")
    record2 = make_record(question_id="Q200")

    client = FakeAPIClient(
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


def test_uploader_skips_non_uploadable_records(tmp_path: Path, database) -> None:
    config = make_config(tmp_path)
    db = database(config.db_path)
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
    client = FakeAPIClient([{"code": 0, "message": "ok", "data": {"result": 1}}])
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


def test_scan_detects_fingerprint_change(tmp_path: Path, database) -> None:
    config = make_config(tmp_path)
    db = database(config.db_path)
    db.init_schema()

    record = make_record(
        question_id="Q300", stem="Original stem", options=[{"label": "A", "text": "old"}]
    )
    client = FakeAPIClient([{"code": 0, "message": "ok", "data": {"result": 1}}])
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


def test_db_schema_and_indexes(tmp_path: Path, database) -> None:
    db = database(tmp_path / "schema.db")
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
                "comprehension_data": make_valid_comprehension_data(),
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
    invalid_data = make_valid_comprehension_data()
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
                "comprehension_data": make_valid_comprehension_data(),
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
    invalid_data = make_valid_comprehension_data()
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
                "comprehension_data": make_valid_comprehension_data(),
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
                "comprehension_data": make_valid_comprehension_data(),
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
                "comprehension_data": make_valid_comprehension_data(),
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
    database,
) -> None:
    config = make_config(tmp_path)
    db = database(config.db_path)
    db.init_schema()

    record = make_record(question_id="Q400")
    # Corrupt the already-serialized comprehension_data so it fails the
    # defensive validation inside upload_one.
    record.comprehension_data = json.dumps({"invalid": "data"})

    uploader = Uploader(config, db, FakeAPIClient([]))
    with caplog.at_level("WARNING"):
        uploader.upload_one(record, "batch-4")

    logs = db.logs.get_logs("Q400")
    assert len(logs) == 1
    assert logs[0]["action"] == "validate"
    assert logs[0]["status"] == "failed"
    assert logs[0]["api_code"] == 10011
    assert logs[0]["format_vno"] == "v1"
