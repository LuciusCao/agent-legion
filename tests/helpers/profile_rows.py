"""Test helpers shared by runtime-profile tests (#359)."""

from __future__ import annotations

from datetime import UTC, datetime


def bucket_matches(value: object, expected: datetime) -> bool:
    """Compare a bucket_start instant regardless of tz representation.

    The row factory renders timestamptz in the session timezone (a stored
    UTC instant may come back as Asia/Shanghai), so string comparison is
    representation-dependent; normalize both to UTC before comparing.
    """
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC) == expected.astimezone(UTC)
