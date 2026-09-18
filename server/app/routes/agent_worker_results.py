"""Validation for Agent Worker result metadata."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from server.app.agent_control.completion import AgentOutcome
from server.app.routes.agent_worker_result_refs import parse_artifact_ref
from shared.code_sandbox import MAX_CONNECTION_KEY_CHARS

_MAX_COMMAND_PARTS = 64
_MAX_OUTPUT_ARTIFACTS = 128
_MAX_ERROR_MESSAGE_CHARS = 4000
_MAX_RUN_DIR_CHARS = 256
_MAX_CONNECTION_KEY_CHARS = MAX_CONNECTION_KEY_CHARS
# #748: optional agent-crash stderr tail the Worker appends to the result
# metadata; capped at the error_message budget (the Worker truncates to the
# same bound, the reader re-truncates defensively for older/other writers).
_MAX_AGENT_STDERR_TAIL_CHARS = 4000
# #748 R2 P2-1: the Worker's X-Agent-Result byte budget (h11 caps one HTTP
# event at 16 KiB) can force a 128-entry direct-upload artifact manifest to
# degrade to a kept PREFIX (or, at the extreme, an empty list). The Worker
# stamps these markers so the reader can tell "truncated by the writer"
# apart from "reported none"; both keys are optional and tolerated-absent
# like agent_stderr_tail above (older Workers / non-truncating shapes).
ARTIFACTS_TRUNCATED_KEY = "output_artifacts_truncated"
ARTIFACTS_TOTAL_KEY = "output_artifacts_total"


def _recover_result_header(raw: str) -> str:
    """Undo the transport decoding of the X-Agent-Result header (#748 P2).

    The Worker sends the metadata JSON as raw UTF-8 BYTES (h11 keeps header
    values as bytes; Starlette decodes them latin-1 — the roundtrip is
    verified against the real uvicorn+h11+requests chain). This reverses
    exactly that: latin-1 re-encode → UTF-8 decode. The escape hatch keeps
    legacy all-ASCII payloads (already identical in both encodings) and
    hand-built test inputs working — any value that is not valid UTF-8 in
    this direction is passed through untouched."""
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


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
    output_artifacts = {str(name): parse_artifact_ref(ref) for name, ref in artifacts_raw.items()}
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
    # #748: bounded, optional stderr tail for agent-crash attribution
    # (absent for completed/cancelled/timeout runs and older Workers).
    agent_stderr_tail = str(metadata.get("agent_stderr_tail", ""))[:_MAX_AGENT_STDERR_TAIL_CHARS]
    # #748 R2 P2-1: writer-side artifact-list truncation markers. The Worker
    # only emits them when the byte budget forced a degrade, and then
    # ALWAYS as a pair; tolerate a lone/missing half the same way (absent =
    # full list, non-int total = treat as truncated with unknown origin).
    artifacts_truncated = metadata.get(ARTIFACTS_TRUNCATED_KEY) is True
    artifacts_total_raw = metadata.get(ARTIFACTS_TOTAL_KEY, 0)
    artifacts_total = artifacts_total_raw if type(artifacts_total_raw) is int else 0
    outcome = AgentOutcome(
        status=status,  # type: ignore[arg-type]
        exit_code=exit_code,
        error_message=error_message,
        command=tuple(str(part) for part in command_raw),
        output_artifacts=output_artifacts,
        run_dir=run_dir,
        auth_failure_connection=auth_failure_raw.strip(),
        agent_stderr_tail=agent_stderr_tail,
        output_artifacts_truncated=artifacts_truncated,
        output_artifacts_total=artifacts_total,
    )
    record = {
        "status": status,
        "exit_code": exit_code,
        "error_message": error_message,
        "output_artifacts": output_artifacts,
        "run_dir": run_dir,
        "auth_failure_connection": auth_failure_raw.strip(),
        "agent_stderr_tail": agent_stderr_tail,
        ARTIFACTS_TRUNCATED_KEY: artifacts_truncated,
        ARTIFACTS_TOTAL_KEY: artifacts_total,
    }
    return outcome, record
