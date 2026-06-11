from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Schema helpers – strict field access matching the actual CMS API contracts
# ---------------------------------------------------------------------------


def _parse_question_list_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse /v2/question/list response.

    Expected schema::

        {
            "code": 0,
            "message": "success",
            "data": {
                "question_list": [
                    {
                        "question_uuid": "...",
                        "body": {"content": "..."},
                        ...
                    }
                ],
                "total": 64
            }
        }
    """
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return []
    question_list = data.get("question_list")
    if not isinstance(question_list, list):
        return []
    return [item for item in question_list if isinstance(item, dict)]


def _parse_question_list_total(payload: dict[str, Any]) -> int | None:
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return None
    value = data.get("total")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_question_detail_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Parse /v2/question/detail response.

    Expected schema::

        {
            "code": 0,
            "message": "success",
            "data": {
                "question_uuid": "...",
                "body": {"content": "..."},
                "option": [...],
                "answer": [...],
                "analyze": [...],
                "video_data": [...]
            }
        }
    """
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict) or not data:
        return None
    if not data.get("question_uuid"):
        return None
    return data


def _question_title_from_item(item: dict[str, Any]) -> str:
    # Prefer knowledge name over body content (stem) for title
    knowledge_list = item.get("knowledge")
    if isinstance(knowledge_list, list) and knowledge_list:
        first = knowledge_list[0]
        if isinstance(first, dict):
            name = first.get("knowledge_name") or first.get("name")
            if name:
                return str(name)

    title = item.get("question_title")
    if title:
        return str(title)

    body = item.get("body")
    if isinstance(body, dict):
        content = body.get("content")
        if content:
            return str(content)
    return str(item.get("question_uuid", ""))


def _question_id_from_item(item: dict[str, Any]) -> str:
    return str(item.get("question_uuid", "")).strip()


def _extract_video_url_from_detail(data: dict[str, Any]) -> tuple[str | None, str | None]:
    video_data = data.get("video_data")
    if not isinstance(video_data, list):
        return None, None
    for vd in video_data:
        if not isinstance(vd, dict):
            continue
        source_url = vd.get("source", "") or vd.get("source_v2", "")
        source_uuid = vd.get("source_uuid", "")
        if source_url:
            return source_url, source_uuid
    return None, None


def _parse_cms_answer(answer: Any) -> list[dict[str, Any]]:
    """Parse CMS answer[blank][alternative] into structured blanks."""
    if not isinstance(answer, list):
        return []
    result: list[dict[str, Any]] = []
    for blank in answer:
        if not isinstance(blank, list):
            continue
        alternatives: list[str] = []
        is_latex = False
        for alt in blank:
            if isinstance(alt, dict):
                content = alt.get("content", "")
                alternatives.append(str(content))
                if alt.get("is_latex"):
                    is_latex = True
        if alternatives:
            result.append({"alternatives": alternatives, "is_latex": is_latex})
    return result


def _parse_cms_analysis(analysis: Any) -> list[list[dict[str, Any]]]:
    """Parse CMS analyze[group][step] into structured steps."""
    if not isinstance(analysis, list):
        return []
    result: list[list[dict[str, Any]]] = []
    for group in analysis:
        if not isinstance(group, list):
            continue
        steps: list[dict[str, Any]] = []
        for step in group:
            if isinstance(step, dict):
                steps.append(
                    {
                        "content": str(step.get("content", "")),
                        "title": str(step.get("title", "")) or None,
                        "step": step.get("step", 0),
                    }
                )
        if steps:
            result.append(steps)
    return result


def _normalize_question_detail(data: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    body = data.get("body")
    if isinstance(body, dict):
        content = body.get("content")
        if content:
            normalized["stem"] = content

    option = data.get("option")
    if option:
        normalized["options"] = option

    answer = data.get("answer")
    if answer:
        normalized["answer"] = answer
        normalized["answer_blanks"] = _parse_cms_answer(answer)

    analyze = data.get("analyze")
    if analyze:
        normalized["analysis"] = analyze
        normalized["analysis_steps"] = _parse_cms_analysis(analyze)

    return normalized


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def lookup_question_video(
    uuid: str, api_url: str | None = None, token: str | None = None
) -> CmsVideoLookup:
    url = api_url or DEFAULT_QUESTION_URL
    payload = _fetch_json(url, {"uuid": uuid}, token)
    data = _parse_question_detail_payload(payload)
    if data is None:
        return CmsVideoLookup("not_found", payload=payload)
    video_url, source_uuid = _extract_video_url_from_detail(data)
    status = "found" if video_url else "missing_url"
    return CmsVideoLookup(
        status, video_url or "", _question_title_from_item(data), source_uuid or "", payload
    )


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
        items = _parse_question_list_payload(payload)
        for item in items:
            question_id = _question_id_from_item(item)
            if not question_id or question_id in seen:
                continue
            seen.add(question_id)
            results.append(
                CmsQuestionSummary(
                    question_id=question_id,
                    title=_question_title_from_item(item),
                    payload=item,
                )
            )

        total = _parse_question_list_total(payload)
        if total is not None and len(results) >= total:
            break
        if not items or len(items) < page_size:
            break
        page += 1

    return results


def fetch_question_detail(
    question_id: str,
    api_url: str | None = None,
    token: str | None = None,
) -> CmsQuestionDetail:
    url = _strip_query_params(api_url or DEFAULT_QUESTION_URL, {"uuid"})
    payload = _fetch_json(url, {"uuid": question_id}, token)
    data = _parse_question_detail_payload(payload)
    if data is None:
        return CmsQuestionDetail(
            question_id=question_id,
            title=question_id,
            normalized={},
            payload=payload,
        )
    parsed_id = _question_id_from_item(data) or question_id
    return CmsQuestionDetail(
        question_id=parsed_id,
        title=_question_title_from_item(data),
        normalized=_normalize_question_detail(data),
        payload=payload,
    )


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


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
