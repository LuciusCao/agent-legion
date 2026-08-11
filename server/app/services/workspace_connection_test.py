"""Workspace connection probe behind settings test-connection.

Resolves which instance-level external connection the workspace's
fetch_questions node points at and delegates to that connection's adapter
probe, so the UI can distinguish "misconfigured" from "unreachable" from
"bad token". Secret resolution happens in memory only (VAULT-SECRET-001).

Intentional security boundary: this runs under the workspace settings route,
which any authenticated workspace member may call, so members can probe an
admin-managed instance-level connection. That is accepted by design — the
probe URL is fixed by the admin-owned connection config (the caller cannot
aim it elsewhere) and the result is only a classified ok/message string, so
the information leak is limited to "is this connection healthy right now".
Do not widen the surface (no caller-supplied URLs, no response bodies).
"""

from typing import Any

from server.app.services.connections import ConnectionService
from server.app.services.job_errors import InvalidOperationError
from server.app.services.node_connection import workspace_node_connection_key
from server.app.settings import Settings


def test_workspace_connection(
    workspace_id: str,
    workspace: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    connection_key = workspace_node_connection_key(
        settings.executor_definitions,
        workspace,
        "question_comprehension_info",
        "fetch_questions",
        "fetch_questions",
    )
    if not connection_key:
        raise InvalidOperationError(
            "该 workspace 的 fetch_questions 节点未配置外部服务连接（connection）"
        )
    service = ConnectionService(settings.database_url, settings.config)
    result = service.probe(connection_key)
    return {
        "ok": True,
        "message": f"连接 {connection_key} 探测成功：{result['message']}",
    }
