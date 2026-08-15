"""GZip responder with an already-compressed Content-Type exemption.

NOTE: this subclasses GZipResponder and flips its private
``content_type_is_excluded`` flag after super() has computed the SSE
exemption — it relies on starlette 1.0.x internals, so re-verify against
the new implementation whenever starlette is upgraded (the test in
tests/test_main.py goes red if the contract breaks). Content-Type matching
is case-sensitive; all current producers emit lowercase media types, and a
miss only degrades to re-gzipping (correctness is unaffected).
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipResponder
from starlette.types import Message

# Payloads already compressed on the wire: agent bundles and result archives
# are tar.gz, and re-gzipping them in the middleware is a pure CPU cost (it
# also strips Content-Length from the response).
ALREADY_COMPRESSED_CONTENT_TYPES = (
    "application/gzip",
    "application/x-gzip",
    "application/zip",
    "application/zstd",
    "application/x-bzip2",
    "application/x-xz",
)


class SelectiveGZipResponder(GZipResponder):
    """Stock gzip responder plus an already-compressed Content-Type exemption."""

    async def send_with_compression(self, message: Message) -> None:
        await super().send_with_compression(message)
        if message["type"] == "http.response.start":
            content_type = Headers(raw=message["headers"]).get("content-type", "")
            if content_type.startswith(ALREADY_COMPRESSED_CONTENT_TYPES):
                self.content_type_is_excluded = True
