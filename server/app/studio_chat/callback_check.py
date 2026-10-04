"""Does the registry ``api_base`` call back into THIS instance? (#915)

Run once per session spawn (create and resume share spawn.py) before the
agent receives its MCP entry. A wrong ``api_base`` used to fail silently:
kimi-code drops an MCP server it cannot reach (or that answers 401 because
the token belongs to another instance) and the session runs with zero
agent-legion tools, surfacing only the vague one-time ``mcp_unverified``
hint after a whole turn. The check talks to the token-less instance probe
(mcp_server/instance_probe.py) and turns the outcome into an actionable
timeline warning.

Not blocking by design: the agent can still chat without tools, and an
admin may legitimately route api_base through a proxy the probe cannot
traverse — so a failed check warns prominently, it never refuses the
session or rewrites the configured address. Results are cached per
api_base for a short TTL so a burst of session starts probes once; the
probe timeout is short because it runs inside session startup.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from typing import TYPE_CHECKING

import httpx

from server.app.mcp_server.http_app import MCP_MOUNT_PATH
from server.app.mcp_server.instance_probe import PROBE_SUBPATH, instance_proof

if TYPE_CHECKING:
    from server.app.studio_chat.store import StudioChatStore

PROBE_TIMEOUT_SECONDS = 2.0
CACHE_TTL_SECONDS = 30.0
CALLBACK_UNREACHABLE_EVENT = "mcp_callback_unreachable"
_LIVE_STATUSES = ("starting", "idle", "running", "awaiting_permission")
SETTINGS_HINT = (
    "请在「全局设置 → Studio Agent 管理 → 平台回调地址（api_base）」修正为本实例后端地址"
)

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, str | None]] = {}


def _probe(api_base: str) -> str | None:
    nonce = secrets.token_hex(16)
    url = f"{api_base.rstrip('/')}{MCP_MOUNT_PATH}{PROBE_SUBPATH}"
    try:
        # trust_env=False: a proxy env var must not reroute the self-check.
        with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS, trust_env=False) as client:
            response = client.get(url, params={"nonce": nonce}, follow_redirects=False)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return f"连接失败（{type(exc).__name__}）"
    if response.status_code != 200:
        return f"HTTP {response.status_code}，该地址不是本实例的 Studio 回调端点"
    try:
        payload = response.json()
    except ValueError:
        payload = None
    proof = payload.get("proof") if isinstance(payload, dict) else None
    if not isinstance(proof, str) or not hmac.compare_digest(proof, instance_proof(nonce)):
        return "该地址响应来自另一个 Agent Legion 实例"
    return None


def check_api_base(api_base: str) -> str | None:
    """None when api_base reaches this process; otherwise a short reason."""
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(api_base)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    reason = _probe(api_base)
    with _cache_lock:
        _cache[api_base] = (time.monotonic(), reason)
    return reason


def unreachable_detail(api_base: str, reason: str) -> str:
    return (
        f"平台回调地址 api_base（{api_base}）无法回连本实例：{reason}。"
        "agent 本会话看不到 agent-legion 平台工具（读写草稿等），只能纯对话；"
        f"{SETTINGS_HINT}，然后新建会话或「继续对话」。"
    )


def warn_callback_unreachable(
    store: StudioChatStore, session_id: str, api_base: str, reason: str | None
) -> None:
    """Timeline warning after a successful start. A close or soft delete that
    raced the startup owns the final state: the liveness check and the INSERT
    are one atomic statement (append_message_if_live), so a closed or deleted
    session gets no warning row and no published event."""
    if reason is None:
        return
    detail = unreachable_detail(api_base, reason)
    store.append_message_if_live(
        session_id,
        "status",
        "system",
        {"event": CALLBACK_UNREACHABLE_EVENT, "detail": detail},
        _LIVE_STATUSES,
    )


def clear_callback_check_cache() -> None:
    with _cache_lock:
        _cache.clear()
