import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from typing import Any

import requests

from server.app.cms.client import CmsClientError

_TOKEN_GEN_ENV = {
    "app_id": "BASECMS_APP_ID",
    "nonce": "BASECMS_NONCE",
    "secret": "BASECMS_SECRET",
    "url": "BASECMS_TOKEN_URL",
}


def _token_gen_config(config: Mapping[str, Any]) -> dict[str, str]:
    """Resolve token_gen keys, environment first, then in-memory config.

    The in-memory ``token_gen`` section only ever comes from env injection or
    a workspace binding; the yaml ``cms.token_gen`` section was retired in
    config governance G2 and is rejected at load time.
    """
    cfg = config.get("token_gen") or {}
    if not isinstance(cfg, Mapping):
        cfg = {}
    return {
        key: str(os.environ.get(env_key) or cfg.get(key) or "")
        for key, env_key in _TOKEN_GEN_ENV.items()
    }


def cms_token_available(cms_config: Mapping[str, Any] | None) -> bool:
    """Return True when CMS credentials resolve from any supported source.

    Priority: env ``BASECMS_TOKEN`` > binding/config ``token`` (vault
    secret_refs must be resolved by the caller beforehand) > the four
    token_gen keys (env first, then in-memory config).
    """
    if cms_config is None:
        cms_config = {}
    # Callers pass raw config values; a malformed cms section must not crash.
    if not isinstance(cms_config, Mapping):
        return False  # type: ignore[unreachable]
    if os.environ.get("BASECMS_TOKEN"):
        return True
    if cms_config.get("token"):
        return True
    return all(_token_gen_config(cms_config).values())


def _generate_prod_token(config: dict[str, Any]) -> str | None:
    cfg = _token_gen_config(config)
    if not all(cfg.values()):
        return None
    timestamp = str(int(time.time()))
    msg = cfg["app_id"] + timestamp + cfg["nonce"]
    sign = hmac.new(cfg["secret"].encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()

    payload = {
        "app_id": cfg["app_id"],
        "sign": sign,
        "timestamp": timestamp,
        "nonce": cfg["nonce"],
        "secret": cfg["secret"],
    }
    try:
        resp = requests.post(
            cfg["url"], json=payload, headers={"Content-Type": "application/json"}, timeout=10
        )
        resp.raise_for_status()
        result = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise CmsClientError(f"CMS token request failed: {exc}") from exc
    token: str | None = result.get("token")
    if not token:
        data = result.get("data")
        if isinstance(data, dict):
            token = data.get("token")
    if not token:
        raise CmsClientError(f"生成 token 失败，响应: {json.dumps(result, ensure_ascii=False)}")
    return token
