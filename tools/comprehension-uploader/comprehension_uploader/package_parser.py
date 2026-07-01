from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from comprehension_uploader.schemas import (
    SchemaValidationError,
    UnsupportedSchemaVersionError,
    validate,
)

logger = logging.getLogger(__name__)


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


def _normalize_version(value: Any) -> str:
    """Return a trimmed string version identifier."""
    return str(value).strip()


def _resolve_format_vno(payload: dict[str, Any], line_no: int) -> str:
    """Determine the effective schema/format version for a package line.

    Priority:
        1. ``format_vno`` field.
        2. ``comprehension_info_schema_version`` field.
        3. Default to ``"v1"`` with a warning.
    """
    for key in ("format_vno", "comprehension_info_schema_version"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return _normalize_version(value)
    logger.warning("Line %d: missing format version, defaulting to v1", line_no)
    return "v1"


def validate_record(record: UploadRecord, line_no: int) -> None:
    """Validate ``record.comprehension_data`` against its declared schema version.

    Raises:
        PackageParseError: If ``comprehension_data`` is not valid JSON, the schema
            version is unsupported, or the data fails schema validation.
    """
    version = record.format_vno or "v1"
    try:
        raw = json.loads(record.comprehension_data)
    except json.JSONDecodeError as exc:
        raise PackageParseError(
            f"Line {line_no}: comprehension_data is not valid JSON ({exc})"
        ) from exc
    try:
        validate(version, raw)
    except UnsupportedSchemaVersionError as exc:
        raise PackageParseError(f"Line {line_no}: unsupported schema version {version!r}") from exc
    except SchemaValidationError as exc:
        raise PackageParseError(
            f"Line {line_no}: comprehension_data schema validation failed for version {version!r}: {exc}"
        ) from exc


def parse_package(path: Path) -> Iterator[UploadRecord]:
    """Yield upload records from a JSONL package file.

    This function performs structural parsing only. Schema validation of
    ``comprehension_data`` is the responsibility of the caller (see
    :func:`validate_record` and :func:`validate_package`).
    """
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

            effective_version = _resolve_format_vno(payload, line_no)
            payload["format_vno"] = effective_version

            try:
                record = UploadRecord.model_validate(payload)
            except ValidationError as exc:
                raise PackageParseError(f"Line {line_no} validation failed: {exc}") from exc

            # Ensure the effective version is reflected on the yielded record.
            record.format_vno = effective_version
            yield record


def validate_package(path: Path) -> tuple[int, int, list[str]]:
    """Validate every line of a JSONL package file and return a summary.

    Returns:
        A tuple of ``(passed_count, failed_count, error_messages)``.
    """
    passed = 0
    failed = 0
    errors: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise PackageParseError(f"Line {line_no} is not a JSON object")
                effective_version = _resolve_format_vno(payload, line_no)
                payload["format_vno"] = effective_version
                record = UploadRecord.model_validate(payload)
                record.format_vno = effective_version
                validate_record(record, line_no)
            except PackageParseError as exc:
                failed += 1
                errors.append(str(exc))
                continue
            except ValidationError as exc:
                failed += 1
                errors.append(f"Line {line_no} validation failed: {exc}")
                continue
            passed += 1
    return passed, failed, errors
