from typing import Any

from server.app.cms.client import DEFAULT_KNOWLEDGE_URL, CmsVideoLookup, _fetch_json


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
