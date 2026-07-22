"""HTTP plumbing for the remote LLM gateway: body passthrough and access checks."""

from __future__ import annotations

import hmac
import json

from fastapi import Body, Request, Response
from pydantic import PlainValidator


def _jsonable_to_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    return json.dumps(value).encode("utf-8")


# FastAPI parses JSON request bodies before validating body params, so a plain
# `bytes = Body(...)` 422s on JSON posts; re-serialize the parsed payload back
# to bytes (semantically identical for the upstream API). The validator is
# attached to the Body field's own metadata, which survives into the model.
JSON_BODY = Body(default=b"")
JSON_BODY.metadata.append(PlainValidator(_jsonable_to_bytes))


def check_gateway_request(request: Request, path: str, gateway_token: str) -> Response | None:
    """Return a 401/400 denial response, or None when the request may proxy."""
    token = request.headers.get("x-gateway-token", "")
    authorization = request.headers.get("authorization", "")
    if not token and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if gateway_token and not hmac.compare_digest(token, gateway_token):
        return Response(content="invalid gateway token", status_code=401)
    if ".." in path.split("/"):  # never forward traversal segments upstream
        return Response(content="invalid path", status_code=400)
    return None
