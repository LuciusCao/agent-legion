"""Scriptable fake ACP agent for studio chat tests (stdio NDJSON JSON-RPC).

Run as a subprocess: ``python tests/helpers/fake_acp_agent.py <script.json>``.
Every received message is appended as a JSON line to ``<script>.sink.jsonl``
so tests can assert on the wire traffic (session/new MCP env, prompt text,
permission outcomes, cancel delivery).

Script shape::

    {
      "capabilities": {...},            # agentCapabilities for initialize
      "session_id": "fake-session-1",   # id returned by session/new
      "stop_reason": "end_turn",        # stopReason for session/prompt
      "on_prompt": [                    # steps executed per prompt turn
        {"notify": {"sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hi"}}},
        {"permission": {"toolCall": {...}, "options": [...]},
         "record": "perm"}              # outcome recorded to the sink
      ],
      "wait_for_cancel": true           # hold the turn until session/cancel
    }

Single-threaded message pump: ``pump_until`` re-enters dispatch so a prompt
turn can await a permission response or a cancel notification while keeps
reading.
"""

from __future__ import annotations

import json
import sys
from typing import Any


class _FakeAgent:
    def __init__(self, script_path: str) -> None:
        with open(script_path, encoding="utf-8") as handle:
            self.script = json.load(handle)
        self.sink_path = script_path + ".sink.jsonl"
        self.pending: dict[Any, dict[str, Any]] = {}
        self.cancelled = False
        self.acp_session_id = str(self.script.get("session_id", "fake-session-1"))
        self._next_request_id = 1

    def _send(self, message: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    def _sink(self, message: dict[str, Any]) -> None:
        with open(self.sink_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(message) + "\n")

    def _read_message(self) -> dict[str, Any] | None:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        message = json.loads(line.decode("utf-8"))
        self._sink({"received": message})
        return message

    def pump_until(self, done) -> None:
        while not done():
            message = self._read_message()
            if message is None:
                raise SystemExit(1)
            self.dispatch(message)

    def dispatch(self, message: dict[str, Any]) -> None:
        if "method" in message and "id" in message:
            self._handle_request(message)
        elif "method" in message:
            if message["method"] == "session/cancel":
                self.cancelled = True
        elif "id" in message:
            self.pending[message["id"]] = message

    def _handle_request(self, message: dict[str, Any]) -> None:
        method = message["method"]
        request_id = message["id"]
        if method == "initialize":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": message["params"].get("protocolVersion", 1),
                        "agentCapabilities": self.script.get("capabilities", {}),
                        "agentInfo": {"name": "fake-acp-agent", "title": "Fake ACP Agent"},
                    },
                }
            )
        elif method == "session/new":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"sessionId": self.acp_session_id},
                }
            )
        elif method == "session/prompt":
            self._run_prompt(message["params"].get("sessionId", self.acp_session_id))
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "stopReason": (
                            "cancelled"
                            if self.cancelled
                            else self.script.get("stop_reason", "end_turn")
                        )
                    },
                }
            )
        else:
            self._send({"jsonrpc": "2.0", "id": request_id, "result": {}})

    def _run_prompt(self, session_id: str) -> None:
        for step in self.script.get("on_prompt", []):
            if "notify" in step:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {"sessionId": session_id, "update": step["notify"]},
                    }
                )
            elif "permission" in step:
                outcome = self._request_permission(session_id, step["permission"])
                self._sink({"permission_outcome": outcome, "record": step.get("record")})
        if self.script.get("wait_for_cancel"):
            self.pump_until(lambda: self.cancelled)

    def _request_permission(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = f"fake-{self._next_request_id}"
        self._next_request_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": payload.get("toolCall", {}),
                    "options": payload.get("options", []),
                },
            }
        )
        self.pump_until(lambda: request_id in self.pending)
        return self.pending.pop(request_id).get("result", {}).get("outcome", {})

    def serve(self) -> None:
        self.pump_until(lambda: False)


if __name__ == "__main__":
    _FakeAgent(sys.argv[1]).serve()
