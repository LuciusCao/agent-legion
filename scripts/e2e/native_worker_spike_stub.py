"""Deterministic local listeners for the native Ubuntu spike (test/CI only)."""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
from typing import Any

from scripts.e2e.native_worker_spike_payload import LISTENER_TOKEN


class ProbeListener:
    def __init__(self) -> None:
        self.connections = 0
        self._lock = threading.Lock()
        listener = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                with listener._lock:
                    listener.connections += 1
                self.request.sendall(LISTENER_TOKEN)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.host, self.port = self._server.server_address

    def count(self) -> int:
        with self._lock:
            return self.connections

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


def _sse(chunks: list[dict[str, Any]]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n" for chunk in chunks]
    lines.append("data: [DONE]\n")
    return ("\n".join(lines) + "\n").encode()


class AgentGateway:
    """Script one bash probe, one required-output write, then a final stop."""

    def __init__(self, output_name: str) -> None:
        self.bodies: list[dict[str, Any]] = []
        gateway = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                gateway.bodies.append(body)
                tool_results = sum(
                    message.get("role") == "tool"
                    for message in body.get("messages") or []
                    if isinstance(message, dict)
                )
                if tool_results == 0:
                    name = "bash"
                    arguments = {
                        "command": (
                            "python3 native_worker_spike_payload.py "
                            "--config agent_probe_config.json"
                        )
                    }
                elif tool_results == 1:
                    name = "write"
                    arguments = {
                        "path": output_name,
                        "content": '{"agent":"native-stub","complete":true}',
                    }
                else:
                    name = ""
                    arguments = {}
                if name:
                    call = {
                        "index": 0,
                        "id": f"call_{tool_results + 1}",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                    chunks = [
                        {"choices": [{"delta": {"tool_calls": [call]}}]},
                        {
                            "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                        },
                    ]
                else:
                    chunks = [
                        {"choices": [{"delta": {"content": "done"}}]},
                        {
                            "choices": [{"delta": {}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 23, "completion_tokens": 5},
                        },
                    ]
                payload = _sse(chunks)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()
