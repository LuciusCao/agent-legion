import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class CmsVideoLookup:
    status: str
    url: str = ""
    title: str = ""
    source_uuid: str = ""
    payload: dict[str, Any] | None = None


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


def _first_knowledge_item(payload: dict) -> dict[str, Any] | None:
    for item in _iter_knowledge_items(payload):
        if isinstance(item, dict):
            return item
    return None


def _extract_knowledge_title(item: dict[str, Any] | None, code: str) -> str:
    if not item:
        return code
    return str(
        item.get("knowledge_name")
        or item.get("name")
        or item.get("title")
        or item.get("knowledge_code")
        or code
    )


def _extract_question_item(payload: dict) -> dict[str, Any] | None:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if not isinstance(data, dict) or not data:
        return None
    has_identity = any(data.get(key) for key in ("question_uuid", "uuid", "id", "question_id"))
    has_content = any(data.get(key) for key in ("title", "question_title", "name", "stem", "content"))
    has_video_data = isinstance(data.get("video_data"), list)
    return data if has_identity or has_content or has_video_data else None


def _extract_question_title(item: dict[str, Any] | None, uuid: str) -> str:
    if not item:
        return uuid
    return str(item.get("title") or item.get("question_title") or item.get("name") or uuid)


def _extract_knowledge_url(code: str, payload: dict) -> tuple[str | None, str | None]:
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
            source_uuid = video_data.get("source_uuid", "")
            if source_url:
                return source_url, source_uuid
    return None, None


def _extract_question_url(payload: dict) -> tuple[str | None, str | None]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return None, None
    for vd in data.get("video_data", []) or []:
        if not isinstance(vd, dict):
            continue
        source_url = vd.get("source", "") or vd.get("source_v2", "")
        source_uuid = vd.get("source_uuid", "")
        if source_url:
            return source_url, source_uuid
    return None, None


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


def lookup_knowledge_video(
    code: str, api_url: str | None = None, token: str | None = None
) -> CmsVideoLookup:
    url = api_url or DEFAULT_KNOWLEDGE_URL
    payload = _fetch_json(url, {"code": code}, token)
    item = _first_knowledge_item(payload)
    if item is None:
        return CmsVideoLookup("not_found", payload=payload)
    video_url, source_uuid = _extract_knowledge_url(code, payload)
    status = "found" if video_url else "missing_url"
    return CmsVideoLookup(status, video_url or "", _extract_knowledge_title(item, code), source_uuid or "", payload)


def lookup_question_video(
    uuid: str, api_url: str | None = None, token: str | None = None
) -> CmsVideoLookup:
    url = api_url or DEFAULT_QUESTION_URL
    payload = _fetch_json(url, {"uuid": uuid}, token)
    item = _extract_question_item(payload)
    if item is None:
        return CmsVideoLookup("not_found", payload=payload)
    video_url, source_uuid = _extract_question_url(payload)
    status = "found" if video_url else "missing_url"
    return CmsVideoLookup(status, video_url or "", _extract_question_title(item, uuid), source_uuid or "", payload)


def fetch_knowledge_url(code: str, api_url: str | None = None, token: str | None = None) -> str | None:
    result = lookup_knowledge_video(code, api_url, token)
    return result.url or None


def fetch_question_url(uuid: str, api_url: str | None = None, token: str | None = None) -> str | None:
    result = lookup_question_video(uuid, api_url, token)
    return result.url or None
