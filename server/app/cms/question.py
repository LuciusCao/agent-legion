from typing import Any

from server.app.cms.client import DEFAULT_QUESTION_URL, CmsVideoLookup, _fetch_json


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
