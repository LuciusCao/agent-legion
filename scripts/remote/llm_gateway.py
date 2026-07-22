#!/usr/bin/env python3
"""Minimal OpenAI-compatible LLM gateway for remote workers.

Runs on the company laptop, binds to its tailnet address, and forwards
requests to the company model platform (中台) with the credential held here.
Remote workers point their pi provider base_url at this gateway; the API key
never leaves the laptop and is never logged. When LLM_GATEWAY_TOKEN is set,
requests must present it (X-Gateway-Token or Authorization: Bearer; pi sends
its provider apiKey as Bearer, so apiKey: "$LLM_GATEWAY_TOKEN" in the
worker's models.json authenticates natively). Unset means open — loopback
development only; a tailnet-bound gateway MUST set it.
"""

from __future__ import annotations

import argparse
import os

import requests
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

if __package__:
    from scripts.remote.llm_gateway_config import add_provider_arguments, resolve_credentials
    from scripts.remote.llm_gateway_http import JSON_BODY, check_gateway_request
    from scripts.remote.llm_gateway_stream import stream_upstream as _stream_upstream
else:
    from llm_gateway_config import add_provider_arguments, resolve_credentials
    from llm_gateway_http import JSON_BODY, check_gateway_request
    from llm_gateway_stream import stream_upstream as _stream_upstream

CONNECT_TIMEOUT_SECONDS = 10.0


def create_gateway_app(
    upstream: str, key: str, timeout_seconds: float = 600.0, gateway_token: str = ""
) -> FastAPI:
    app = FastAPI(title="Agent Legion Remote LLM Gateway")
    base = upstream.rstrip("/")

    @app.post("/v1/{path:path}")
    def proxy(path: str, request: Request, payload: bytes = JSON_BODY) -> Response:
        denied = check_gateway_request(request, path, gateway_token)
        if denied is not None:
            return denied
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

        return StreamingResponse(
            _stream_upstream(upstream_resp),
            status_code=upstream_resp.status_code,
            media_type=upstream_resp.headers.get("content-type", "application/json"),
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Legion remote LLM gateway")
    parser.add_argument("--host", required=True, help="tailnet IP of this machine, e.g. 100.x.y.z")
    parser.add_argument("--port", type=int, default=8788)
    add_provider_arguments(parser)
    args = parser.parse_args(argv)
    upstream, key = resolve_credentials(parser, args)
    gateway_token = os.environ.get("LLM_GATEWAY_TOKEN", "")
    if not gateway_token:
        print("[gateway] WARNING: LLM_GATEWAY_TOKEN unset — gateway is OPEN", flush=True)

    uvicorn.run(
        create_gateway_app(upstream, key, gateway_token=gateway_token),
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
