"""Error-response OpenAPI envelope invariant (#1177 codex P2).

Every 4xx/5xx JSON response declared in the spec must expose a top-level
``detail`` property: app-level handlers translate service errors to
``HTTPException(detail=...)`` and FastAPI's stock http_exception_handler
always renders ``{"detail": ...}`` (the auto-declared 422
``HTTPValidationError`` already complies). Declaring the service-layer
payload model bare as the whole body types generated clients — the
frontend derives transport types from ``generated/api.ts`` — against a
shape the wire never has, so this scans the full spec rather than trusting
each route author to hand-wrap the envelope.
"""


def test_error_responses_declare_detail_envelope(client) -> None:
    spec = client.app.openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    violations: list[str] = []
    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for status, response in operation.get("responses", {}).items():
                if not str(status).isdigit() or not 400 <= int(status) < 600:
                    continue
                schema = response.get("content", {}).get("application/json", {}).get("schema")
                if not isinstance(schema, dict):
                    continue
                target = schema
                ref = schema.get("$ref")
                if isinstance(ref, str):
                    target = schemas.get(ref.rsplit("/", 1)[-1], {})
                properties = target.get("properties") or {}
                if properties and "detail" not in properties:
                    violations.append(f"{method.upper()} {path} -> {status}")
    assert not violations, (
        "error responses must wrap their payload in a top-level 'detail' "
        f"envelope (FastAPI stock handler shape): {violations}"
    )
