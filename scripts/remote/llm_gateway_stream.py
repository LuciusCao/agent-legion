from collections.abc import Iterator

import requests

if __package__:
    from scripts.remote.llm_gateway_sse import line_is_terminal as _line_is_terminal
else:
    from llm_gateway_sse import line_is_terminal as _line_is_terminal


def stream_upstream(upstream_resp: requests.Response) -> Iterator[bytes]:
    """Normalize completed SSE streams even if the HTTP terminator is missing."""
    is_sse = "text/event-stream" in upstream_resp.headers.get("content-type", "").lower()
    saw_terminal = saw_done = False
    pending_line = b""
    try:
        for chunk in upstream_resp.iter_content(chunk_size=8192):
            if is_sse and chunk:
                lines = (pending_line + chunk).splitlines(keepends=True)
                pending_line = b"" if lines[-1].endswith((b"\n", b"\r")) else lines.pop()
                for line in lines:
                    terminal, done = _line_is_terminal(line)
                    saw_terminal = saw_terminal or terminal
                    saw_done = saw_done or done
            yield chunk
    except requests.exceptions.ChunkedEncodingError:
        terminal, done = _line_is_terminal(pending_line)
        saw_terminal = saw_terminal or terminal
        saw_done = saw_done or done
        if not saw_terminal:
            raise
    finally:
        upstream_resp.close()
    if is_sse and saw_terminal and not saw_done:
        yield b"data: [DONE]\n\n"
