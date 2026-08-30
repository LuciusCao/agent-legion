"""Per-token registration state derived from runtime status and store rows.

Split from ``worker/supervisor.py`` (#250 budget floors): the supervisor keeps
process orchestration, while this read-only projection answers "how is each
configured scoped register token doing" for the local UI's token cards.
Behavior is unchanged — ``WorkerSupervisor.token_status`` delegates here.
"""

from __future__ import annotations

from typing import Any

from worker.status import STATUS_FILENAME, read_runtime_status


def token_status(failed: bool, state_dir: Any, read_registration_tokens: Any) -> dict[str, str]:
    """Per-token registration state keyed by token_id.

    The Host resolves the union scope and rejects the whole registration
    when any token is bad, so per-token granularity is limited: 'ok' once
    a registration round succeeded (the workspaces detail in the runtime
    file names which workspaces the tokens opened), 'rejected' when the
    supervisor recorded a registration failure, 'pending' otherwise."""
    runtime = read_runtime_status(state_dir / STATUS_FILENAME)
    remote = runtime["remote"] or {}
    workspaces = remote.get("workspaces")
    tokens = read_registration_tokens()
    if isinstance(workspaces, list) and workspaces:
        # 本轮注册已成功，Host 汇报的 workspaces 即所有有效 token 的并集。
        return {row["token_id"]: "ok" for row in tokens}
    registered = bool(remote.get("registered"))
    state = "rejected" if failed else ("ok" if registered else "pending")
    return {row["token_id"]: state for row in tokens}
