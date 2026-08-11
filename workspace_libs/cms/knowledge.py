from __future__ import annotations

from typing import Any

from workspace_libs.cms.client import (
    CmsVideoLookup,
    _fetch_json,
    check_in_band_error,
    require_api_url,
)

# ---------------------------------------------------------------------------
# Schema helpers – strict field access matching the actual CMS API contract
# ---------------------------------------------------------------------------


def _parse_knowledge_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Parse /v2/knowledge/detail response.

    Expected schema::

        {
            "code": 0,
            "message": "success",
            "data": {
                "knowledge_code": "...",
                "knowledge_name": "...",
                "resource": [
                    {
                        "resource_type": 1,
                        "video_data": {
                            "source_url": "...",
                            "source_uuid": "..."
                        }
                    }
                ]
            }
        }
    """
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict) or not data:
        return None
    if not data.get("knowledge_code"):
        return None
    return data


def _knowledge_title(data: dict[str, Any]) -> str:
    return str(
        data.get("knowledge_name")
        or data.get("name")
        or data.get("title")
        or data.get("knowledge_code")
        or ""
    )


def _extract_knowledge_video_url(data: dict[str, Any]) -> tuple[str | None, str | None]:
    for res in data.get("resource", []) or []:
        if not isinstance(res, dict):
            continue
        rtype_val = res.get("resource_type")
        if rtype_val is None:
            continue
        try:
            rtype = int(rtype_val)
        except (TypeError, ValueError):
            continue
        if rtype not in {1, 2}:
            continue
        video_data = res.get("video_data") or {}
        if not isinstance(video_data, dict):
            continue
        source_url = (
            video_data.get("source_url", "")
            or video_data.get("source", "")
            or video_data.get("source_v2", "")
        )
        source_uuid = video_data.get("source_uuid", "")
        if source_url:
            return source_url, source_uuid
    return None, None


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def lookup_knowledge_video(
    code: str, api_url: str | None = None, token: str | None = None
) -> CmsVideoLookup:
    url = require_api_url(api_url, "knowledge detail")
    payload = _fetch_json(url, {"code": code}, token)
    check_in_band_error(payload, f"knowledge_code={code}")
    data = _parse_knowledge_payload(payload)
    if data is None:
        return CmsVideoLookup("not_found", payload=payload)
    video_url, source_uuid = _extract_knowledge_video_url(data)
    status = "found" if video_url else "missing_url"
    return CmsVideoLookup(
        status, video_url or "", _knowledge_title(data), source_uuid or "", payload
    )
