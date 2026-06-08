from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from server.app.cms.client import (
    DEFAULT_QUESTION_LIST_URL,
    DEFAULT_QUESTION_URL,
    CmsVideoLookup,
    _fetch_json,
)


@dataclass(frozen=True)
class CmsQuestionSummary:
    question_id: str
    title: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CmsQuestionDetail:
    question_id: str
    title: str
    normalized: dict[str, Any]
    payload: dict[str, Any] | None


def _extract_question_item(payload: dict) -> dict[str, Any] | None:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if not isinstance(data, dict) or not data:
        return None
    has_identity = any(data.get(key) for key in ("question_uuid", "uuid", "id", "question_id"))
    has_content = any(
        data.get(key) for key in ("title", "question_title", "name", "stem", "content")
    )
    has_video_data = isinstance(data.get("video_data"), list)
    return data if has_identity or has_content or has_video_data else None


def _extract_question_title(item: dict[str, Any] | None, uuid: str) -> str:
    if not item:
        return uuid
    return str(item.get("title") or item.get("question_title") or item.get("name") or uuid)


def _extract_question_id(item: dict[str, Any]) -> str:
    return str(
        item.get("uuid")
        or item.get("question_uuid")
        or item.get("question_id")
        or item.get("id")
        or ""
    ).strip()


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
    return CmsVideoLookup(
        status, video_url or "", _extract_question_title(item, uuid), source_uuid or "", payload
    )


def _question_items_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("list", "items", "records", "questions", "data"):
        items = data.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _payload_total(payload: dict[str, Any]) -> int | None:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    containers = [payload]
    if isinstance(data, dict):
        containers.insert(0, data)
    for container in containers:
        value = container.get("total") or container.get("count") or container.get("total_count")
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _page_size_from_url(url: str) -> int:
    query = parse_qs(urlparse(url).query)
    value = (query.get("page_size") or query.get("pageSize") or ["50"])[0]
    try:
        return int(value)
    except (TypeError, ValueError):
        return 50


def _strip_query_params(url: str, dynamic_keys: set[str]) -> str:
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in dynamic_keys
    ]
    return urlunparse(parsed._replace(query=urlencode(query)))


def list_questions_by_knowledge(
    knowledge_code: str,
    api_url: str | None = None,
    token: str | None = None,
) -> list[CmsQuestionSummary]:
    url = _strip_query_params(api_url or DEFAULT_QUESTION_LIST_URL, {"knowledge", "page"})
    page_size = _page_size_from_url(url)
    page = 1
    results: list[CmsQuestionSummary] = []
    seen: set[str] = set()

    while True:
        payload = _fetch_json(url, {"knowledge": knowledge_code, "page": page}, token)
        items = _question_items_from_payload(payload)
        for item in items:
            question_id = _extract_question_id(item)
            if not question_id or question_id in seen:
                continue
            seen.add(question_id)
            results.append(
                CmsQuestionSummary(
                    question_id=question_id,
                    title=_extract_question_title(item, question_id),
                    payload=item,
                )
            )

        total = _payload_total(payload)
        if total is not None and len(results) >= total:
            break
        if not items or len(items) < page_size:
            break
        page += 1

    return results


def _normalize_question_detail(item: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    field_map = {
        "stem": ("stem", "content", "question_content", "body"),
        "options": ("options", "option", "answers"),
        "answer": ("answer", "correct_answer", "right_answer"),
        "analysis": ("analysis", "explanation", "solution", "resolve"),
    }
    for target_key, source_keys in field_map.items():
        for source_key in source_keys:
            value = item.get(source_key)
            if value not in (None, "", []):
                normalized[target_key] = value
                break
    return normalized


def fetch_question_detail(
    question_id: str,
    api_url: str | None = None,
    token: str | None = None,
) -> CmsQuestionDetail:
    url = _strip_query_params(api_url or DEFAULT_QUESTION_URL, {"uuid"})
    payload = _fetch_json(url, {"uuid": question_id}, token)
    item = _extract_question_item(payload)
    if item is None:
        return CmsQuestionDetail(
            question_id=question_id,
            title=question_id,
            normalized={},
            payload=payload,
        )
    parsed_id = _extract_question_id(item) or question_id
    return CmsQuestionDetail(
        question_id=parsed_id,
        title=_extract_question_title(item, parsed_id),
        normalized=_normalize_question_detail(item),
        payload=payload,
    )
