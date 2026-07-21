"""Streaming compatibility for OpenAI-style upstream gateways."""

from collections.abc import Iterator

import requests

SSE_DONE_MARKER = b"[DONE]"


def stream_upstream(upstream_resp: requests.Response) -> Iterator[bytes]:
    """Tolerate a missing HTTP terminator only after the SSE done marker."""
    saw_done = False
    marker_overlap = b""
    try:
        for chunk in upstream_resp.iter_content(chunk_size=8192):
            if chunk:
                marker_window = marker_overlap + chunk
                saw_done = saw_done or SSE_DONE_MARKER in marker_window
                marker_overlap = marker_window[-(len(SSE_DONE_MARKER) - 1) :]
            yield chunk
    except requests.exceptions.ChunkedEncodingError:
        if not saw_done:
            raise
    finally:
        upstream_resp.close()
