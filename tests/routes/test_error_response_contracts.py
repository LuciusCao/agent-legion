"""Error-response OpenAPI envelope invariant (#1177 codex P2).

Every 4xx/5xx JSON response declared in the spec must wrap its payload in
a top-level ``detail`` envelope: app-level handlers translate service
errors to ``HTTPException(detail=...)`` and FastAPI's stock
http_exception_handler always renders ``{"detail": ...}``. Declaring the
service-layer payload model bare as the whole body types generated
clients — the frontend derives transport types from ``generated/api.ts``
— against a shape the wire never has.

The traversal lives in ``scripts.export_openapi_contracts.validate_error_response_envelopes``
(single source, also enforced at export time by the api:check lane); this
test applies it to the runtime spec — a superset of the /api-filtered
export, so nothing declared can slip past both lanes.
"""

from scripts.export_openapi_contracts import validate_error_response_envelopes


def test_error_responses_declare_detail_envelope(client) -> None:
    validate_error_response_envelopes(client.app.openapi())
