import hashlib
import hmac
import json
import os
import time
from typing import Any

import requests

DEFAULT_KNOWLEDGE_URL = (
    "http://cms.internal.example.com/v2/knowledge/detail?bank_version=v5&country_id=1&subject_id=2"
)
DEFAULT_QUESTION_URL = (
    "http://cms.internal.example.com/v2/question/detail?bank_version=v5&country_id=1&subject_id=2"
)


def _token_gen_config(config: dict[str, Any]) -> dict[str, str]:
    cfg = config.get("token_gen") or {}
    return {
        "app_id": str(cfg.get("app_id") or os.environ.get("BASECMS_APP_ID") or ""),
        "nonce": str(cfg.get("nonce") or os.environ.get("BASECMS_NONCE") or ""),
        "secret": str(cfg.get("secret") or os.environ.get("BASECMS_SECRET") or ""),
        "url": str(cfg.get("url") or os.environ.get("BASECMS_TOKEN_URL") or ""),
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
    resp = requests.post(cfg["url"], json=payload, headers={"Content-Type": "application/json"}, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    token = result.get("token") or result.get("data", {}).get("token")
    if not token:
        raise Exception(f"生成 token 失败，响应: {json.dumps(result, ensure_ascii=False)}")
    return token


def _build_headers(token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "*/*"}
    if token:
        token = token.strip()
        if not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        headers["Authorization"] = token
    return headers


def _fetch_json(url: str, params: dict[str, Any], token: str | None, timeout: int = 15) -> dict:
    resp = requests.get(url, params=params, headers=_build_headers(token), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _iter_knowledge_items(payload: dict) -> Any:
    if isinstance(payload, list):
        yield from payload
        return

    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, list):
        yield from data
        return
    if not isinstance(data, dict):
        return

    if "resource" in data or "knowledge_code" in data or "knowledge_name" in data:
        yield data
        return

    if "list" in data and isinstance(data["list"], list):
        yield from data["list"]
        return

    for v in data.values():
        if isinstance(v, dict):
            yield v
        elif isinstance(v, list):
            yield from v


def _extract_knowledge_url(code: str, payload: dict) -> str | None:
    for item in _iter_knowledge_items(payload):
        if not isinstance(item, dict):
            continue
        for res in item.get("resource", []) or []:
            try:
                rtype = int(res.get("resource_type"))
            except (TypeError, ValueError):
                continue
            if rtype not in {1, 2}:
                continue
            video_data = res.get("video_data") or {}
            source_url = (
                video_data.get("source_url", "")
                or video_data.get("source", "")
                or video_data.get("source_v2", "")
            )
            if source_url:
                return source_url
    return None


def _extract_question_url(payload: dict) -> str | None:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return None
    for vd in data.get("video_data", []) or []:
        if not isinstance(vd, dict):
            continue
        source_url = vd.get("source", "") or vd.get("source_v2", "")
        if source_url:
            return source_url
    return None


def get_token(env: str, config: dict[str, Any] | None = None) -> str | None:
    config = config or {}
    token = config.get("token")
    if token:
        return token
    token = os.environ.get("BASECMS_TOKEN")
    if token:
        return token
    if env == "prod":
        return _generate_prod_token(config)
    return None


def fetch_knowledge_url(code: str, api_url: str | None = None, token: str | None = None) -> str | None:
    url = api_url or DEFAULT_KNOWLEDGE_URL
    payload = _fetch_json(url, {"code": code}, token)
    return _extract_knowledge_url(code, payload)


def fetch_question_url(uuid: str, api_url: str | None = None, token: str | None = None) -> str | None:
    url = api_url or DEFAULT_QUESTION_URL
    payload = _fetch_json(url, {"uuid": uuid}, token)
    return _extract_question_url(payload)
