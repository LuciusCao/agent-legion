"""Workspace CMS connection probe behind settings test-connection.

Resolves the workspace's merged CMS resource (including vault secret_refs,
in memory only) and performs a real HTTP probe so the UI can distinguish
"misconfigured" from "unreachable" from "bad token".
"""

from typing import Any

import requests

from server.app.cms.client import get_token
from server.app.services.job_errors import InvalidOperationError
from server.app.services.vault import VaultError, VaultService
from server.app.settings import Settings
from server.app.workflows.resources import resolve_cms_resource


def test_workspace_connection(
    workspace_id: str,
    workspace: dict[str, Any],
    settings: Settings,
    vault: VaultService,
) -> dict[str, Any]:
    cms_resource = resolve_cms_resource(
        settings.config,
        workspace,
        None,
        "question_detail",
        declarations=settings.resource_providers,
    )
    api_url = str(cms_resource.get("api_url") or "")
    if not api_url:
        raise InvalidOperationError(
            "CMS URL 未配置:请在资源卡片中绑定 api_url,或配置 cms.base_url / CMS_BASE_URL"
        )
    try:
        # Resolve secret_ref markers in memory only; legacy plaintext
        # passes through (spec D14 compatibility window).
        cms_resource = vault.resolve_secret_refs(cms_resource, workspace_id)
    except VaultError as exc:
        raise InvalidOperationError(str(exc)) from exc
    token = get_token(str(cms_resource.get("env", "")), cms_resource)
    if not token:
        raise InvalidOperationError(
            "CMS token 未配置:请在资源卡片中设置 token(存入 vault),或配置 env CMS_TOKEN"
        )
    token_source = "workspace binding" if cms_resource.get("token_from_binding") else "全局 env"
    headers = {"Accept": "*/*", "Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
    except requests.RequestException as exc:
        raise InvalidOperationError(f"无法连接 CMS: {exc}") from exc
    if resp.status_code in (401, 403):
        raise InvalidOperationError(
            f"CMS 可达但鉴权失败(HTTP {resp.status_code}),请检查 {token_source} 的 token"
        )
    return {
        "ok": True,
        "message": f"连接成功(HTTP {resp.status_code},token 来源:{token_source})",
    }
