"""Micro-benchmark for issue #88 (opt 1): CPU cost of re-gzipping an already
compressed response vs. the exempt pass-through in SelectiveGZipMiddleware.

Usage: uv run python scripts/bench_gzip_exemption.py [requests] [payload-mib]
"""

from __future__ import annotations

import gzip
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.responses import Response  # noqa: E402

from server.app.http_middleware import SelectiveGZipMiddleware  # noqa: E402


def _app(middleware: type, payload_mib: int) -> FastAPI:
    payload = gzip.compress(os.urandom(payload_mib * 1024 * 1024))  # incompressible tar.gz
    app = FastAPI()

    @app.get("/bundle")
    def _bundle() -> Response:
        return Response(content=payload, media_type="application/gzip")

    app.add_middleware(middleware)
    return app


def _bench(middleware: type, requests: int, payload_mib: int) -> float:
    with TestClient(_app(middleware, payload_mib)) as client:
        client.get("/bundle")  # warmup
        started = time.perf_counter()
        for _ in range(requests):
            response = client.get("/bundle")
            assert response.status_code == 200
        return time.perf_counter() - started


if __name__ == "__main__":
    requests = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    payload_mib = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    recompressed = _bench(GZipMiddleware, requests, payload_mib)
    exempt = _bench(SelectiveGZipMiddleware, requests, payload_mib)
    print(f"payload: {payload_mib} MiB tar.gz, {requests} requests")
    print(f"re-gzip (old): {recompressed:.2f}s total, {recompressed / requests * 1000:.0f}ms/req")
    print(f"exempt  (new): {exempt:.2f}s total, {exempt / requests * 1000:.0f}ms/req")
    if exempt > 0:
        print(f"speedup: {recompressed / exempt:.1f}x")
