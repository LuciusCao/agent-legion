"""OpenAPI response-contract validations for the export gate.

Split from ``export_openapi.py`` (file budget): pure schema-dict checks
shared by the export flow (``build_openapi_schema``) and the runtime-spec
pytest lane (tests/routes/test_error_response_contracts.py), so both lanes
enforce one source of truth.
"""

from typing import Any


def validate_response_contracts(schema: dict[str, Any], exempt_operation_ids: set[str]) -> None:
    errors = []
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId", f"{method} {path}")
            if operation_id in exempt_operation_ids:
                continue
            for status, response in operation.get("responses", {}).items():
                if not str(status).startswith("2"):
                    continue
                content = response.get("content", {})
                json_schema = content.get("application/json", {}).get("schema")
                if json_schema is not None and "$ref" not in json_schema:
                    errors.append(f"{operation_id} has inline JSON response schema")
    if errors:
        raise ValueError("; ".join(errors))


def validate_error_response_envelopes(schema: dict[str, Any]) -> None:
    """#1177 codex P2: declared 4xx/5xx JSON responses must wrap their payload
    in a top-level ``detail`` envelope — app-level handlers translate service
    errors to ``HTTPException(detail=...)`` and FastAPI's stock
    http_exception_handler always renders ``{"detail": ...}`` (the
    auto-declared 422 ``HTTPValidationError`` already complies). Declaring
    the bare payload model types generated clients against a shape the wire
    never has. Out of scope (no shape claim to check): non-JSON bodies,
    non-integer/``default`` status keys, and schemas without ``properties``
    (free-form). Enforced at export time (api:check lane, via
    ``build_openapi_schema``) and on the runtime spec by
    tests/routes/test_error_response_contracts.py.
    """
    errors = []
    components = schema.get("components", {}).get("schemas", {})
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId", f"{method} {path}")
            for status, response in operation.get("responses", {}).items():
                if not str(status).isdigit() or not 400 <= int(status) < 600:
                    continue
                json_schema = response.get("content", {}).get("application/json", {}).get("schema")
                if not isinstance(json_schema, dict):
                    continue
                target = json_schema
                ref = json_schema.get("$ref")
                if isinstance(ref, str):
                    target = components.get(ref.rsplit("/", 1)[-1])
                    if target is None:
                        errors.append(f"{operation_id} {status} has unresolvable schema {ref}")
                        continue
                properties = target.get("properties") or {}
                if properties and "detail" not in properties:
                    errors.append(f"{operation_id} {status} lacks a top-level detail envelope")
    if errors:
        raise ValueError("; ".join(errors))
