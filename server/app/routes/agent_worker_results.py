"""Validation for Agent Worker result metadata."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

from server.app.agent_completion import AgentOutcome

_ARTIFACT_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_ARTIFACT_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_COMMAND_PARTS = 64
_MAX_OUTPUT_ARTIFACTS = 128
_MAX_ERROR_MESSAGE_CHARS = 4000
_MAX_RUN_DIR_CHARS = 256
_MAX_CONNECTION_KEY_CHARS = 128
_MAX_STORAGE_KEY_CHARS = 1024


def _parse_artifact_ref(ref: Any) -> str | dict[str, Any]:
    """One ``output_artifacts`` value: legacy CAS ref or object-storage ref.

    Legacy form: ``"sha256:<64 hex>"`` (returned as-is). Object-storage form
    (#160 D12): ``{"storage_key", "size_bytes", "content_hash"}`` — the key
    must stay inside the ``jobs/`` prefix with no traversal, the size must be
    a non-negative int, and the hash is empty or 64 lowercase hex.
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
    if key_path.is_absolute() or ".." in key_path.parts or key_path.parts[:1] != ("jobs",):
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


def parse_result_metadata(raw: str) -> tuple[AgentOutcome, dict[str, Any]]:
    """Validate worker result metadata into an outcome and stored record."""
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("metadata is not valid JSON") from exc
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    status = str(metadata.get("status", ""))
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("invalid status")
    try:
        exit_code = int(metadata.get("exit_code", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid exit_code") from exc
    command_raw = metadata.get("command", [])
    if not isinstance(command_raw, (list, tuple)) or len(command_raw) > _MAX_COMMAND_PARTS:
        raise ValueError("invalid command")
    artifacts_raw = metadata.get("output_artifacts", {})
    if not isinstance(artifacts_raw, dict) or len(artifacts_raw) > _MAX_OUTPUT_ARTIFACTS:
        raise ValueError("invalid output artifacts")
    output_artifacts = {str(name): _parse_artifact_ref(ref) for name, ref in artifacts_raw.items()}
    error_message = str(metadata.get("error_message", ""))[:_MAX_ERROR_MESSAGE_CHARS]
    run_dir_raw = metadata.get("run_dir", "")
    if not isinstance(run_dir_raw, str) or len(run_dir_raw) > _MAX_RUN_DIR_CHARS:
        raise ValueError("invalid run_dir")
    run_dir_relative = PurePosixPath(run_dir_raw)
    run_dir = ""
    if run_dir_raw:
        if run_dir_relative.is_absolute() or ".." in run_dir_relative.parts:
            raise ValueError("invalid run_dir")
        run_dir = run_dir_relative.as_posix()
    # Batch 2: a code node reports the connection key whose cached token the
    # Host must invalidate (design §5.3); bounded, plain string.
    auth_failure_raw = metadata.get("auth_failure_connection", "")
    if not isinstance(auth_failure_raw, str) or len(auth_failure_raw) > _MAX_CONNECTION_KEY_CHARS:
        raise ValueError("invalid auth_failure_connection")
    outcome = AgentOutcome(
        status=status,  # type: ignore[arg-type]
        exit_code=exit_code,
        error_message=error_message,
        command=tuple(str(part) for part in command_raw),
        output_artifacts=output_artifacts,
        run_dir=run_dir,
        auth_failure_connection=auth_failure_raw.strip(),
    )
    record = {
        "status": status,
        "exit_code": exit_code,
        "error_message": error_message,
        "output_artifacts": output_artifacts,
        "run_dir": run_dir,
        "auth_failure_connection": auth_failure_raw.strip(),
    }
    return outcome, record
