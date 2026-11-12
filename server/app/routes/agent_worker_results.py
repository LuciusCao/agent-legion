"""Validation for Agent Worker result metadata."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from server.app.agent_broker.result_output_manifest import (
    MAX_OUTPUT_ARTIFACTS,
    parse_artifact_ref,
)
from server.app.agent_control.completion import AgentOutcome
from server.app.routes.agent_worker_result_extras import (
    ARTIFACTS_TOTAL_KEY,
    ARTIFACTS_TRUNCATED_KEY,
    parse_result_extras,
)
from shared.code_contract import MAX_RESULT_COMMAND_PARTS
from shared.code_sandbox import MAX_CONNECTION_KEY_CHARS

# #822: command 是观测面，超限截断（保前缀）而非 400——拒收会让 Worker 按 4xx
# 终态丢弃结果，租约过期后整轮 agent 执行重跑且永不收敛。单一事实来源见
# shared/code_contract.py（Worker 序列化侧同用）。
_MAX_COMMAND_PARTS = MAX_RESULT_COMMAND_PARTS
_MAX_OUTPUT_ARTIFACTS = MAX_OUTPUT_ARTIFACTS
_MAX_ERROR_MESSAGE_CHARS = 4000
_MAX_RUN_DIR_CHARS = 256
_MAX_CONNECTION_KEY_CHARS = MAX_CONNECTION_KEY_CHARS


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
    # #822: 形态错误仍拒收；段数超限只截断（保前缀）——旧 Worker（无序列化侧
    # 收缩）的 40+ 产物 argv 也能落地，不再进 400 → 丢结果 → 重排队死循环。
    command_raw = metadata.get("command", [])
    if not isinstance(command_raw, (list, tuple)):
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
    # Optional, tolerated-absent extras (stderr tail, v1 artifact-manifest
    # truncation markers, #865 output-truncation facts) — split out for the
    # file budget; see agent_worker_result_extras.py.
    extras = parse_result_extras(metadata)
    outcome = AgentOutcome(
        status=status,  # type: ignore[arg-type]
        exit_code=exit_code,
        error_message=error_message,
        command=tuple(str(part) for part in command_raw[:_MAX_COMMAND_PARTS]),
        output_artifacts=output_artifacts,
        run_dir=run_dir,
        auth_failure_connection=auth_failure_raw.strip(),
        agent_stderr_tail=extras["agent_stderr_tail"],
        output_artifacts_truncated=extras[ARTIFACTS_TRUNCATED_KEY],
        output_artifacts_total=extras[ARTIFACTS_TOTAL_KEY],
    )
    record = {
        "status": status,
        "exit_code": exit_code,
        "error_message": error_message,
        "output_artifacts": output_artifacts,
        "run_dir": run_dir,
        "auth_failure_connection": auth_failure_raw.strip(),
        **extras,
    }
    return outcome, record
