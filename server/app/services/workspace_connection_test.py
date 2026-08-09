"""Workspace CMS connection probe behind settings test-connection.

Resolves the workspace's effective node CMS config (including vault
secret_refs, in memory only) and performs a real HTTP probe so the UI can
distinguish "misconfigured" from "unreachable" from "bad token".
"""

from typing import Any

import requests

from server.app.cms import urls as cms_urls
from server.app.cms.client import get_token
from server.app.services.cms_node_config import cms_node_config
from server.app.services.job_errors import InvalidOperationError
from server.app.services.vault import VaultError, VaultService
from server.app.settings import Settings


def test_workspace_connection(
    workspace_id: str,
    workspace: dict[str, Any],
    settings: Settings,
    vault: VaultService,
) -> dict[str, Any]:
    cms_config = cms_node_config(
        settings.config,
        workspace,
        "question_comprehension_info",
        "fetch_questions",
    )
    api_url = str(
        cms_config.get("api_url")
        or cms_config.get("question_detail_url")
        or cms_urls.question_detail_url(cms_config)
    )
    if not api_url:
        raise InvalidOperationError(
            "CMS URL 未配置:请在节点配置中设置 api_url 或 base_url,或配置 env CMS_BASE_URL"
        )
    try:
        # Resolve secret_ref markers in memory only (VAULT-SECRET-001).
        cms_config = vault.resolve_secret_refs(cms_config, workspace_id)
    except VaultError as exc:
        raise InvalidOperationError(str(exc)) from exc
    if cms_config.get("token"):
        cms_config["token_from_binding"] = True
    token = get_token(str(cms_config.get("env", "")), cms_config)
    if not token:
        raise InvalidOperationError(
            "CMS token 未配置:请在节点配置中设置 token(存入 vault),或配置 env CMS_TOKEN"
        )
    token_source = "workspace node config" if cms_config.get("token_from_binding") else "全局 env"
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
