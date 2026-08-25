"""Baseline loader for the service data-boundary ratchet.

Split out of ``service_data_boundary.py`` for the file-size budget; strict
parsing of ``config/architecture/service-data-boundary-baseline.json`` lives
here. Each entry maps a service file to a ``[sql_literals, db_primitive_refs]``
pair; both only ever ratchet down.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

__test__ = False


@dataclass(frozen=True)
class ServiceDataBoundaryBaseline:
    files: dict[str, tuple[int, int]]


class ServiceDataBoundaryConfigurationError(ValueError):
    """Internal configuration error captured by check_service_data_boundary."""

    pass


def load_service_data_boundary_baseline(path: Path) -> ServiceDataBoundaryBaseline:
    """Require exactly version 1 and a normalized (sql, primitives) map."""
    if not path.is_file():
        raise ServiceDataBoundaryConfigurationError(f"Baseline file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ServiceDataBoundaryConfigurationError(f"Malformed JSON in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ServiceDataBoundaryConfigurationError(
            f"Baseline root must be a mapping, got {type(raw).__name__}"
        )

    if set(raw) != {"version", "files"}:
        extra = set(raw) - {"version", "files"}
        missing = {"version", "files"} - set(raw)
        parts: list[str] = []
        if missing:
            parts.append(f"missing fields: {sorted(missing)}")
        if extra:
            parts.append(f"unknown fields: {sorted(extra)}")
        raise ServiceDataBoundaryConfigurationError(
            f"Invalid baseline structure; {'; '.join(parts)}"
        )

    version = raw.get("version")
    if type(version) is not int or version != 1:
        raise ServiceDataBoundaryConfigurationError(f"Unsupported baseline version: {version!r}")

    files = raw.get("files")
    if not isinstance(files, dict):
        raise ServiceDataBoundaryConfigurationError("files must be a mapping")

    normalized: dict[str, tuple[int, int]] = {}
    for key, value in files.items():
        if not isinstance(key, str):
            raise ServiceDataBoundaryConfigurationError("baseline path keys must be strings")
        if not isinstance(value, list) or len(value) != 2:
            raise ServiceDataBoundaryConfigurationError(
                f"baseline counts for {key} must be a [sql, primitives] pair"
            )
        sql_literals, db_primitive_refs = value
        if type(sql_literals) is not int or type(db_primitive_refs) is not int:
            raise ServiceDataBoundaryConfigurationError(
                f"baseline counts for {key} must be integers"
            )
        if sql_literals < 0 or db_primitive_refs < 0:
            raise ServiceDataBoundaryConfigurationError(
                f"baseline counts for {key} must be non-negative"
            )
        if sql_literals == 0 and db_primitive_refs == 0:
            raise ServiceDataBoundaryConfigurationError(
                f"baseline entry for {key} must record at least one bypass"
            )
        normalized_key = str(PurePosixPath(key))
        if normalized_key in normalized:
            raise ServiceDataBoundaryConfigurationError(
                f"duplicate normalized baseline path: {normalized_key}"
            )
        normalized[normalized_key] = (sql_literals, db_primitive_refs)

    return ServiceDataBoundaryBaseline(files=normalized)
