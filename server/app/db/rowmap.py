"""Row-to-dict projection helpers shared by the service layer (#278).

The same private helpers used to be copy-pasted across services under one
name (``_timestamp``) with two different meanings — datetime→datetime UTC
normalization for SQL parameters (atomic mutations) versus row value→ISO
string serialization for API records (vault/connections/materials/runs).
This module names the two semantics apart so a future copy cannot import
the wrong one by accident.

The row-layer ISOness itself (``_row_value`` in :mod:`server.app.db.rows`)
stays there: it is an untyped pass-through tied to ``DatabaseRow``
construction, while the helpers here are the service-facing, None-aware
serialization pair.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def iso_optional(value: Any) -> str | None:
    """Serialize a row timestamp for an API record: None stays None.

    Datetimes render via ``isoformat()`` (the row layer has already
    normalized them to UTC ISO strings with an explicit offset — see
    ``server.app.db.rows``); anything else (str columns, legacy shapes)
    renders as ``str(value)`` unchanged. ``None`` passes through so
    nullable columns (``expires_at`` on materials, token timestamps)
    stay absent rather than the string ``"None"``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def utc_datetime(value: datetime) -> datetime:
    """Normalize an aware datetime to UTC for SQL parameters.

    Parameter twin of :func:`iso_optional`: this keeps the value a datetime
    (psycopg renders timestamptz parameters itself) and only pins the
    timezone, so a naive comparison against ``current_timestamp`` in SQL
    never sees a non-UTC offset.
    """
    return value.astimezone(UTC)


def parse_object(raw: Any) -> dict[str, Any]:
    """Tolerant JSON-object parse of a stored column: unreadable degrades to {}.

    ``str(raw or "")`` also renders ``None``/non-str columns as "" so the
    legacy-TEXT and native-json column shapes share one fallback; a decoded
    non-object (list/str/number) is not an object and degrades the same way.
    """
    try:
        value = json.loads(str(raw or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def wire_batch_id(job: Mapping[str, Any]) -> str:
    """The legacy ``batch_id`` API field for a job row (#279 step 2).

    Schema v53 renamed the column ``jobs.batch_id`` → ``jobs.run_id`` with
    the value unchanged; API/SSE consumers still read ``batch_id`` (route
    renames are a later slice), so every projection layer stamps this alias
    from ``run_id``. Lives beside the other projection helpers so intake,
    run creation and the query service share one definition without
    services importing each other. The empty-string fallback covers
    run-less rows exactly like the three call sites it replaces.
    """
    return str(job.get("run_id") or "")
