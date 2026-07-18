#!/usr/bin/env python3
"""Minimal OpenAI-compatible LLM gateway for remote workers.

Runs on the company laptop, binds to its tailnet address, and forwards
requests to the company model platform (中台) with the credential held here.
Remote workers point their pi provider base_url at this gateway; the API key
never leaves the laptop and is never logged.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator

import requests
import uvicorn
from fastapi import Body, FastAPI, Response
from fastapi.responses import StreamingResponse
from pydantic import PlainValidator

CONNECT_TIMEOUT_SECONDS = 10.0


def _jsonable_to_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    return json.dumps(value).encode("utf-8")


# FastAPI parses JSON request bodies before validating body params, so a plain
# `bytes = Body(...)` would 422 on JSON posts; re-serialize the parsed payload
# back to bytes instead (semantically identical for the upstream API). FastAPI
# drops Annotated metadata it does not own, so the validator is attached to the
# Body field's own metadata, which survives into the pydantic model field.
_JSON_BODY = Body(default=b"")
_JSON_BODY.metadata.append(PlainValidator(_jsonable_to_bytes))


def create_gateway_app(upstream: str, key: str, timeout_seconds: float = 600.0) -> FastAPI:
    app = FastAPI(title="Agent Legion Remote LLM Gateway")
    base = upstream.rstrip("/")

    @app.post("/v1/{path:path}")
    def proxy(path: str, payload: bytes = _JSON_BODY) -> Response:
        try:
            upstream_resp = requests.post(
                f"{base}/v1/{path}",
                data=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                stream=True,
                timeout=(CONNECT_TIMEOUT_SECONDS, timeout_seconds),
            )
        except requests.RequestException as exc:
            # Never include the key or request body in the error.
            return Response(
                content=f"upstream request failed: {type(exc).__name__}",
                status_code=502,
                media_type="text/plain",
            )

        def stream() -> Iterator[bytes]:
            try:
                yield from upstream_resp.iter_content(chunk_size=8192)
            finally:
                upstream_resp.close()

        return StreamingResponse(
            stream(),
            status_code=upstream_resp.status_code,
            media_type=upstream_resp.headers.get("content-type", "application/json"),
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Legion remote LLM gateway")
    parser.add_argument("--host", required=True, help="tailnet IP of this machine, e.g. 100.x.y.z")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args(argv)

    upstream = os.environ.get("REMOTE_LLM_UPSTREAM", "")
    key = os.environ.get("REMOTE_LLM_KEY", "")
    if not upstream:
        parser.error("REMOTE_LLM_UPSTREAM is required (中台 base URL)")
    if not key:
        parser.error("REMOTE_LLM_KEY is required (中台 credential)")

    uvicorn.run(
        create_gateway_app(upstream, key),
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
