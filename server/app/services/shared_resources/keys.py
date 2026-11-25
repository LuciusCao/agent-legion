"""Single-use object-key derivation for immutable resource versions."""

from __future__ import annotations

KEY_PREFIX = "shared-resources/"


def resource_file_key(resource_id: str, version_id: str, generation: int, file_id: str) -> str:
    return f"{KEY_PREFIX}{resource_id}/{version_id}/g{generation}/{file_id}"
