"""Static contract tests for the velites event stream schema.

velites (``velites/``) replaces the Node Pi CLI as the worker's headless
executor; its stdout NDJSON event stream must keep feeding the existing
Host consumers. These tests pin the fields those consumers read against
the authoritative schema exported from the Rust code
(``velites/schema/events.schema.json``), so a Rust-side rename or type
drift fails here instead of at runtime.

Consumer surface (re-verify against the code when updating this file):
- ``server/app/services/pi_event_scan.py:27-43`` — event-type allowlist
- ``server/app/services/token_usage.py:57-76,134-142`` — usage/provider/model
- ``server/app/workflows/pi_model_error.py:15-44`` — errorMessage/stopReason
- ``server/app/services/job_log_renderer.py:86,120-182`` — render fields
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.app.services.pi_event_scan import RELEVANT_EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "velites" / "schema" / "events.schema.json"

# Event types velites promises to emit (the schema's whole oneOf set).
VELITES_EVENT_TYPES = {
    "session",
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_end",
    "auto_retry_start",
    "tool_execution_start",
    "tool_execution_end",
    "outputs_validation",
}

# Pi streams carry these delta events; velites deliberately does not.
DELTA_EVENT_TYPES = {"message_update", "tool_execution_update"}

STOP_REASONS = {"stop", "length", "toolUse", "error", "aborted"}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    assert SCHEMA_PATH.is_file(), f"velites schema missing: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _deref(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``#/$defs/...`` reference one hop."""
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node
    prefix = "#/$defs/"
    assert ref.startswith(prefix), f"unexpected non-local ref: {ref}"
    return schema["$defs"][ref[len(prefix) :]]


def _object_schema(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Resolve a node (possibly anyOf/oneOf wrapping a ref) to an object schema."""
    node = _deref(schema, node)
    if "properties" in node:
        return node
    for combinator in ("anyOf", "oneOf"):
        for branch in node.get(combinator, []):
            resolved = _deref(schema, branch)
            if "properties" in resolved:
                return resolved
    raise AssertionError(f"no object schema found in node: {sorted(node)}")


def _event_type_consts(schema: dict[str, Any]) -> set[str]:
    consts = set()
    for variant in schema["oneOf"]:
        consts.add(variant["properties"]["type"]["const"])
    return consts


def _message_schema(schema: dict[str, Any], event_def: str) -> dict[str, Any]:
    """Object schema of the ``message`` payload of a message-carrying event."""
    event_schema = schema["$defs"][event_def]
    return _object_schema(schema, event_schema["properties"]["message"])


def test_event_types_cover_host_allowlist(schema: dict[str, Any]) -> None:
    """pi_event_scan.py:27-43 — every allowlisted type must exist in the schema."""
    consts = _event_type_consts(schema)
    assert consts == VELITES_EVENT_TYPES
    assert consts >= set(RELEVANT_EVENT_TYPES)


def test_no_delta_event_types(schema: dict[str, Any]) -> None:
    """velites must not regress into emitting Pi's streaming delta events."""
    consts = _event_type_consts(schema)
    assert consts.isdisjoint(DELTA_EVENT_TYPES)
    assert not any("delta" in event_type for event_type in consts)


def test_message_end_usage_fields(schema: dict[str, Any]) -> None:
    """token_usage.py:57-76 — message_end.message.usage{input,output,cacheRead}."""
    message = _message_schema(schema, "MessageEndEvent")
    usage = _object_schema(schema, message["properties"]["usage"])
    for field in ("input", "output", "cacheRead"):
        assert field in usage["properties"], f"usage.{field} missing"
        assert usage["properties"][field]["type"] == "integer"
        assert field in usage["required"], f"usage.{field} not required"


def _ref_branch(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Return the ref branch of an anyOf wrapper (e.g. stopReason | null)."""
    node = _deref(schema, node)
    for combinator in ("anyOf", "oneOf"):
        for branch in node.get(combinator, []):
            if "$ref" in branch:
                return branch
    return node


def test_message_end_assistant_metadata(schema: dict[str, Any]) -> None:
    """token_usage.py:134-142 / pi_model_error.py:39-44 — provider/model/stopReason."""
    message = _message_schema(schema, "MessageEndEvent")
    properties = message["properties"]
    for field in ("provider", "model", "stopReason", "content"):
        assert field in properties, f"message.{field} missing"
    assert "content" in message["required"]
    stop_reason = _deref(schema, _ref_branch(schema, properties["stopReason"]))
    assert set(stop_reason["enum"]) >= STOP_REASONS


def test_error_message_field_optional(schema: dict[str, Any]) -> None:
    """pi_model_error.py:40-41 — errorMessage present on messages, may be absent."""
    for event_def in ("MessageStartEvent", "MessageEndEvent", "TurnEndEvent"):
        message = _message_schema(schema, event_def)
        assert "errorMessage" in message["properties"], f"{event_def} message.errorMessage"
        assert "errorMessage" not in message.get("required", [])


def test_auto_retry_start_contract(schema: dict[str, Any]) -> None:
    """Pi-compatible retry observability: auto_retry_start carries the attempt counter."""
    assert "auto_retry_start" in RELEVANT_EVENT_TYPES, (
        "pi_event_scan.py allowlist must keep auto_retry_start in compressed events.jsonl"
    )
    event = schema["$defs"]["AutoRetryStartEvent"]
    properties = event["properties"]
    assert properties["attempt"]["type"] == "integer"
    assert "attempt" in event["required"]


def test_tool_execution_end_contract(schema: dict[str, Any]) -> None:
    """Tool name, result content and output_bytes on tool_execution_end."""
    event = schema["$defs"]["ToolExecutionEndEvent"]
    properties = event["properties"]
    assert properties["toolName"]["type"] == "string"
    assert "toolName" in event["required"]
    result = _object_schema(schema, properties["result"])
    assert "content" in result["properties"]
    assert "content" in result["required"]
    assert properties["output_bytes"]["type"] == "integer"
    assert "output_bytes" in event["required"]


def test_tool_execution_start_contract(schema: dict[str, Any]) -> None:
    event = schema["$defs"]["ToolExecutionStartEvent"]
    for field in ("toolCallId", "toolName", "args"):
        assert field in event["properties"], f"tool_execution_start.{field} missing"
        assert field in event["required"]


def test_outputs_validation_contract(schema: dict[str, Any]) -> None:
    """velites output self-check (M3): outputs_validation carries `missing`."""
    assert "outputs_validation" in RELEVANT_EVENT_TYPES, (
        "pi_event_scan.py allowlist must keep outputs_validation in compressed events.jsonl"
    )
    event = schema["$defs"]["OutputsValidationEvent"]
    missing = event["properties"]["missing"]
    assert missing["type"] == "array"
    assert missing["items"]["type"] == "string"
    assert "missing" in event["required"]


def test_agent_end_reason_contract(schema: dict[str, Any]) -> None:
    """M3: agent_end gains an optional `reason` (budget_exceeded | cancelled)."""
    event = schema["$defs"]["AgentEndEvent"]
    assert "reason" in event["properties"], "agent_end.reason missing"
    assert "reason" not in event.get("required", []), "reason must stay optional"
    reason = _deref(schema, _ref_branch(schema, event["properties"]["reason"]))
    # schemars renders documented unit variants as oneOf+const, otherwise enum.
    values = set(reason.get("enum", []))
    for variant in reason.get("oneOf", []):
        if "const" in variant:
            values.add(variant["const"])
    assert {"budget_exceeded", "cancelled"} <= values


def test_renderer_consumed_message_fields(schema: dict[str, Any]) -> None:
    """job_log_renderer.py:120-182 — role/content blocks/toolResult metadata."""
    message = _message_schema(schema, "MessageEndEvent")
    role = _deref(schema, message["properties"]["role"])
    assert {"user", "assistant", "toolResult"} <= set(role["enum"])
    for field in ("toolName", "toolCallId", "isError"):
        assert field in message["properties"], f"message.{field} missing"

    content = _deref(schema, message["properties"]["content"]["items"])
    block_types = {variant["properties"]["type"]["const"] for variant in content["oneOf"]}
    assert {"text", "thinking", "toolCall"} <= block_types
    for variant in content["oneOf"]:
        const = variant["properties"]["type"]["const"]
        if const == "toolCall":
            assert {"name", "arguments"} <= set(variant["required"])
        elif const == "text":
            assert "text" in variant["required"]
        elif const == "thinking":
            assert "thinking" in variant["required"]
