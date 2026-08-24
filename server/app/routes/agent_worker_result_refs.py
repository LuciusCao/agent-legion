"""Validation for Worker-reported artifact references (split out of
``agent_worker_results.py`` for the file-size budget)."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

_ARTIFACT_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_ARTIFACT_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_STORAGE_KEY_CHARS = 1024


def parse_artifact_ref(ref: Any) -> str | dict[str, Any]:
    """One ``output_artifacts`` value: legacy CAS ref or object-storage ref.

    Legacy form: ``"sha256:<64 hex>"`` (returned as-is). Object-storage form
    (#160 D12): ``{"storage_key", "size_bytes", "content_hash"}`` — the key
    must stay inside the per-execution ``jobs-staging/`` prefix with no
    traversal (the Host promotes onto the authority key after verification),
    the size must be a non-negative int, and the hash is empty or 64
    lowercase hex.
    """
    if isinstance(ref, str):
        if not _ARTIFACT_REF.fullmatch(ref):
            raise ValueError("invalid output artifact reference")
        return ref
    if not isinstance(ref, dict):
        raise ValueError("invalid output artifact reference")
    storage_key = ref.get("storage_key")
    if not isinstance(storage_key, str) or len(storage_key) > _MAX_STORAGE_KEY_CHARS:
        raise ValueError("invalid output artifact storage key")
    key_path = PurePosixPath(storage_key)
    if key_path.is_absolute() or ".." in key_path.parts or key_path.parts[:1] != ("jobs-staging",):
        raise ValueError("invalid output artifact storage key")
    size_bytes = ref.get("size_bytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        raise ValueError("invalid output artifact size")
    content_hash = ref.get("content_hash", "")
    if not isinstance(content_hash, str) or (
        content_hash and not _ARTIFACT_HASH.fullmatch(content_hash)
    ):
        raise ValueError("invalid output artifact content hash")
    return {
        "storage_key": storage_key,
        "size_bytes": size_bytes,
        "content_hash": content_hash,
    }
