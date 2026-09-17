"""Per-tool input JSON Schemas for the studio-agent MCP surface (#687 attack fix).

The permission auto-approve in ``studio_chat/permissions.py`` must verify that
a tool call claiming to be a platform MCP tool carries an ``rawInput`` the real
server would accept — the title alone is agent-authored free text. This module
derives each tool's JSON Schema from the SAME FastMCP registration
``create_mcp_server`` performs (so schema and call semantics can never drift
apart), then rewrites it into the STRICTER form the permission check needs:

- ``additionalProperties: false`` — FastMCP's own tools/call validation is
  lenient here (unrecognized kwargs are forwarded to the handler), so the
  listed schema alone cannot reject smuggled keys like ``command``. The
  permission gate is a different consumer: a payload that carries keys outside
  the declared surface is NOT "the tool call it claims to be" and must degrade
  to human confirmation.
- no-keyword relaxation: FastMCP-generated schemas keep Python reserved words
  verbatim (a tool parameter named ``type`` keeps ``properties.type``);
  ``jsonschema`` 4.x would reinterpret such keys as its own keywords and
  mis-validate. Nested schemas are wrapped via ``$ref``/``$defs`` so every
  property name is evaluated as a property, never as a keyword.

Fail-closed by construction: a missing or malformed schema raises/returns
None and the caller parks for a human. ``build_tool_input_schemas`` performs
no HTTP calls — the tool bodies only touch the network at call time.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.server import create_mcp_server

# Distinguishable outcomes for the permission gate; callers treat everything
# except "ok" as park-for-human (fail-closed).
SCHEMA_MISSING = "schema_missing"
VALIDATION_ERROR = "validation_error"

_PROBE_CONFIG = McpServerConfig(
    api_base="http://permission-schema.invalid",
    token="permission-schema-probe",
    session_id="permission-schema-probe",
)


def _strict_schema(parameters: dict[str, Any]) -> dict[str, Any] | None:
    """Tighten one FastMCP-generated parameters schema into a strict one.

    Returns None when the generated schema is not a usable object schema
    (fail-closed: the caller then treats the tool as unknown).
    """
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        return None
    strict: dict[str, Any] = {**parameters, "additionalProperties": False}
    properties = parameters.get("properties")
    if isinstance(properties, dict) and properties:
        # Keyword-collision isolation (jsonschema 4.x reserves $schema's own
        # keyword names — "type"/"items" etc. as sibling keys of $ref — when a
        # tool parameter is named like one): wrap nested schemas so every
        # property key is validated as a property, never executed as a keyword.
        # Existing $defs entries (FastMCP emits them for pydantic-model
        # params) are PRESERVED so their $refs stay resolvable — overwriting
        # them would leave dangling refs that raise out of iter_errors
        # (round-2 review MEDIUM-3). A property key colliding with an
        # existing $defs name cannot be isolated safely, so the tool is
        # dropped (fail-closed: schema_missing → park) instead of silently
        # validating against the wrong definition.
        existing_defs = parameters.get("$defs")
        existing_defs = dict(existing_defs) if isinstance(existing_defs, dict) else {}
        if any(key in existing_defs for key in properties):
            return None
        strict["$defs"] = {**existing_defs, **properties}
        strict["properties"] = {key: {"$ref": f"#/$defs/{key}"} for key in properties}
    return strict


def build_tool_input_schemas() -> dict[str, dict[str, Any]]:
    """Derive {tool name: strict input JSON Schema} from the registrations.

    Cost: one FastMCP server build per process (registration is pure function
    wiring; tool bodies run no I/O at import/registration time — see
    tool_client.py, network happens per call). Result is cached by the caller.
    """
    mcp = create_mcp_server(_PROBE_CONFIG)
    schemas: dict[str, dict[str, Any]] = {}
    for tool in mcp._tool_manager.list_tools():  # pinned mcp==1.29 internals
        strict = _strict_schema(tool.parameters)
        if strict is not None:
            schemas[tool.name] = strict
    return schemas


class ToolInputValidator:
    """Strict rawInput validation against the registered tool schemas.

    One instance per process; building the schema table is idempotent and
    thread-safe (read-only after construction), so the permission path can
    share it across sessions and threads.
    """

    def __init__(self) -> None:
        self._validators: dict[str, Draft202012Validator] = {}
        for name, schema in build_tool_input_schemas().items():
            validator_cls = validator_for(schema)
            validator_cls.check_schema(schema)
            self._validators[name] = validator_cls(schema)

    def known_tools(self) -> frozenset[str]:
        return frozenset(self._validators)

    def validate(self, tool_name: str, raw_input: Any) -> tuple[bool, str | None]:
        """Validate raw_input against the tool's strict schema.

        Returns ``(ok, reason)``; ``ok=False`` with a reason on any of:
        unknown tool, schema missing for that tool (conditional registrations
        must not silently pass), non-dict raw_input, or a schema violation.
        The reason is safe to log/display — it never includes the payload.

        Note: ``iter_errors`` can RAISE (not return errors) when the schema
        carries a dangling ``$ref``; the permission-gate caller wraps this
        call fail-closed (exception → park for a human, round-2 review
        MEDIUM-3), so an unexpected schema defect degrades one request
        instead of killing the ACP callback.
        """
        validator = self._validators.get(tool_name)
        if validator is None:
            return False, f"{SCHEMA_MISSING}: no input schema for tool {tool_name!r}"
        if not isinstance(raw_input, dict):
            return False, f"{VALIDATION_ERROR}: rawInput is not a JSON object"
        errors = sorted(validator.iter_errors(raw_input), key=lambda e: list(e.absolute_path))
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.absolute_path) or "<root>"
            return False, f"{VALIDATION_ERROR}: {location}: {first.message}"
        return True, None
