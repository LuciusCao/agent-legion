from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class UploadRecord(BaseModel):
    question_id: str
    subject_id: int | None = None
    question_uuid: str | None = None
    question_vno: int | None = None
    comprehension_difficulty: int | None = Field(default=None, ge=1, le=99)
    format_vno: str | None = None
    comprehension_data: str
    stem: str | None = None
    options: list[Any] | None = None
    fingerprint: str | None = None
    uploadable: bool | None = None
    outcome: str | None = None

    @field_validator("comprehension_data", mode="before")
    @classmethod
    def _serialize_comprehension_data(cls, value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def comprehension_data_hash(self) -> str:
        return hashlib.md5(self.comprehension_data.encode("utf-8")).hexdigest()


class PackageParseError(Exception):
    """Raised when a package.jsonl line cannot be parsed or validated."""


def parse_package(path: Path) -> Iterator[UploadRecord]:
    """Yield validated upload records from a JSONL package file."""
    with path.open(encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PackageParseError(f"Invalid JSON on line {line_no}: {exc}") from exc
            if not isinstance(payload, dict):
                raise PackageParseError(f"Line {line_no} is not a JSON object")
            try:
                record = UploadRecord.model_validate(payload)
            except ValidationError as exc:
                raise PackageParseError(f"Line {line_no} validation failed: {exc}") from exc
            yield record
