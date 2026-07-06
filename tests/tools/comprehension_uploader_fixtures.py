# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# The tool package lives inside a hyphenated directory, so add its parent to
# the import path for the tests.
_TOOL_DIR = Path(__file__).parents[2] / "tools" / "comprehension-uploader"
sys.path.insert(0, str(_TOOL_DIR))

from comprehension_uploader.config import Config
from comprehension_uploader.package_parser import UploadRecord


def make_config(tmp_path: Path, upload_on_duplicate: str = "update") -> Config:
    return Config(
        api_base_url="http://example.com",
        db_path=str(tmp_path / "test.db"),
        question_source={"type": "json_file", "path": str(tmp_path / "questions.json")},
        upload_on_duplicate=upload_on_duplicate,  # type: ignore[arg-type]
        request_timeout=5,
        max_retries=0,
    )


def make_valid_comprehension_data(difficulty: int = 50) -> dict[str, Any]:
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


def make_record(
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
        comprehension_data = make_valid_comprehension_data(difficulty)
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


class FakeAPIClient:
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
