"""Manual smoke driver for the Studio chat ACP backend (phase 3 chunk 4).

Point it at a running backend with a real ACP agent (e.g. ``kimi acp`` or
``gemini --experimental-acp``) and it registers the agent in the instance
registry, opens a chat session in a workspace, sends one prompt, streams the
SSE events until the turn ends, then closes the session:

    uv run python scripts/studio_chat_smoke.py \
        --base http://127.0.0.1:8000 --username admin --password '***' \
        --workspace-id <workspace-id> \
        --agent-id kimi-acp --agent-label "Kimi Code" \
        --agent-command kimi --agent-args acp \
        --message "用 list_workflows 看看平台上有哪些 workflow"

Prereqs: the backend must be running and reachable at --base (the MCP server
the agent spawns calls back to the api_base stored in the registry; override
with --api-base when the backend is not on 127.0.0.1:8000), and the agent CLI
must be on PATH.
"""

from __future__ import annotations

import argparse
import json
import sys

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--agent-label", default=None)
    parser.add_argument("--agent-command", required=True)
    parser.add_argument("--agent-args", nargs="*", default=[])
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    client = requests.Session()
    login = client.post(
        f"{args.base}/api/auth/login",
        json={"username": args.username, "password": args.password},
        timeout=30,
    )
    if login.status_code != 200:
        print(f"login failed: {login.status_code} {login.text}", file=sys.stderr)
        return 2
    client.headers["x-agent-legion-request"] = "1"

    registry = client.get(f"{args.base}/api/admin/studio-agents", timeout=30).json()
    agents = [a for a in registry.get("agents", []) if a["id"] != args.agent_id]
    agents.append(
        {
            "id": args.agent_id,
            "label": args.agent_label or args.agent_id,
            "command": args.agent_command,
            "args": args.agent_args,
        }
    )
    put = client.put(
        f"{args.base}/api/admin/studio-agents",
        json={"api_base": args.api_base, "agents": agents},
        timeout=30,
    )
    if put.status_code != 200:
        print(f"registry update failed: {put.status_code} {put.text}", file=sys.stderr)
        return 2

    base = f"{args.base}/api/workspaces/{args.workspace_id}/studio-chat"
    created = client.post(
        f"{base}/sessions", json={"agent_id": args.agent_id, "title": "smoke"}, timeout=120
    )
    if created.status_code != 200:
        print(f"session create failed: {created.status_code} {created.text}", file=sys.stderr)
        return 2
    session = created.json()["session"]
    session_id = session["id"]
    print(f"session {session_id} status={session['status']} agent={session['agent_id']}")

    try:
        sent = client.post(
            f"{base}/sessions/{session_id}/messages", json={"text": args.message}, timeout=30
        )
        if sent.status_code != 200:
            print(f"message send failed: {sent.status_code} {sent.text}", file=sys.stderr)
            return 2
        print("prompt sent; streaming events (Ctrl-C to stop)...")
        with client.get(
            f"{base}/sessions/{session_id}/events", stream=True, timeout=None
        ) as stream:
            for line in stream.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                payload = json.loads(line[len("data: ") :])
                kind = payload.get("type")
                if kind == "message":
                    message = payload["message"]
                    print(
                        f"[{message['kind']}/{message['role']}] "
                        f"{json.dumps(message['content'], ensure_ascii=False)[:400]}"
                    )
                    content = message["content"]
                    if content.get("event") == "turn_end":
                        break
                elif kind == "session":
                    snapshot = payload["session"]
                    print(
                        f"[session] status={snapshot['status']} mcp_status={snapshot['mcp_status']}"
                    )
        final = client.get(f"{base}/sessions/{session_id}", timeout=30).json()["session"]
        print(f"final: status={final['status']} mcp_status={final['mcp_status']}")
        return 0 if final["status"] == "idle" else 1
    finally:
        client.delete(f"{base}/sessions/{session_id}", timeout=60)


if __name__ == "__main__":
    raise SystemExit(main())
