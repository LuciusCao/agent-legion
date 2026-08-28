"""In-process stub LLM gateway for the browser smoke main-flow spec.

Mirrors tests/full/test_velites_harness_e2e.py: an OpenAI-compatible SSE
server that answers a ``write`` toolCall (the Agent node's single declared
output) on the first turn and ``stop`` once the request carries a tool
result — both with usage chunks, so the real velites binary drives a full
two-turn agent loop without any external LLM. Branching is by request
content, not a global request counter, so multiple independent agent runs
(multi-engine nightly, Playwright retries) stay deterministic on one stub.

The velites provider/model registry (``models.json``, design §7) points the
``gateway`` provider at this stub; the Worker gets its path via the
``VELITES_MODELS_PATH`` entry in its ``environment`` config block, which
covers both model discovery (registration) and execution-time resolution.
"""

from __future__ import annotations

import http.server
import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GATEWAY_PROVIDER = "gateway"
STUB_MODEL = "stub-model"


def _sse(chunks: list[dict[str, Any]]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n" for chunk in chunks]
    lines.append("data: [DONE]\n")
    return ("\n".join(lines) + "\n").encode()


class StubGateway:
    """OpenAI 兼容 SSE stub：按请求内容分支，与执行次数无关。

    首个 turn（messages 里还没有 tool result）回 write toolCall；后续 turn
    （messages 含 ``role: "tool"`` 的 tool result）回 stop。不按全局请求
    计数切换：nightly 多浏览器串行与 Playwright retry 会让同一 stub 服务
    多个独立 agent run，计数式会让第二个 run 永远拿不到 write（blocker）。
    """

    def __init__(self, output_name: str, output_content: str) -> None:
        self.bodies: list[dict[str, Any]] = []
        gateway = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (stdlib API)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                gateway.bodies.append(body)
                messages = body.get("messages") or []
                has_tool_result = any(
                    message.get("role") == "tool"
                    for message in messages
                    if isinstance(message, dict)
                )
                if not has_tool_result:
                    write_call = {
                        "index": 0,
                        "id": "call_1",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {"path": output_name, "content": output_content}
                            ),
                        },
                    }
                    payload = _sse(
                        [
                            {"choices": [{"delta": {"tool_calls": [write_call]}}]},
                            {
                                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                            },
                        ]
                    )
                else:
                    payload = _sse(
                        [
                            {"choices": [{"delta": {"content": "done"}}]},
                            {
                                "choices": [{"delta": {}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 23, "completion_tokens": 5},
                            },
                        ]
                    )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                logger.debug("llm-stub: " + format, *args)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


def write_models_json(path: Path, base_url: str) -> Path:
    """Write the velites model registry pointing the gateway provider at the stub.

    Schema: velites/src/models.rs (``api`` dialect, baseUrl/apiKey, model id
    list). A stub apiKey is required by the registry but never verified by
    the stub server.
    """
    document = {
        "providers": {
            GATEWAY_PROVIDER: {
                "api": "openai-completions",
                "baseUrl": base_url,
                "apiKey": "e2e-stub-key",
                "models": [STUB_MODEL],
            }
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    # velites warns on non-0600 credential files; it holds a (stub) apiKey.
    path.chmod(0o600)
    return path
