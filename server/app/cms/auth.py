import hashlib
import hmac
import json
import os
import time
from typing import Any

import requests


def _token_gen_config(config: dict[str, Any]) -> dict[str, str]:
    cfg = config.get("token_gen") or {}
    return {
        "app_id": str(os.environ.get("BASECMS_APP_ID") or cfg.get("app_id") or ""),
        "nonce": str(os.environ.get("BASECMS_NONCE") or cfg.get("nonce") or ""),
        "secret": str(os.environ.get("BASECMS_SECRET") or cfg.get("secret") or ""),
        "url": str(os.environ.get("BASECMS_TOKEN_URL") or cfg.get("url") or ""),
    }


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
    resp = requests.post(
        cfg["url"], json=payload, headers={"Content-Type": "application/json"}, timeout=10
    )
    resp.raise_for_status()
    result = resp.json()
    token: str | None = result.get("token")
    if not token:
        data = result.get("data")
        if isinstance(data, dict):
            token = data.get("token")
    if not token:
        raise Exception(f"生成 token 失败，响应: {json.dumps(result, ensure_ascii=False)}")
    return token
