"""CMS endpoint URL derivation (pure functions, no settings side effects).

Node code resolves its effective CMS config (global ``cms:`` defaults
overridden by the node's config) and derives endpoint URLs here; an explicit
``api_url`` / ``question_list_url`` node config value always wins over the
derived URL. Query params mirror the retired resource-provider chain:
bank_version / country_id / subject_id on every endpoint, plus page_size on
the question list endpoint.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode


def _endpoint(base_url: str, path: str, params: dict[str, Any]) -> str:
    if not str(base_url or "").strip():
        return ""
    query = {key: str(value) for key, value in params.items() if value not in (None, "")}
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    return f"{url}?{urlencode(query)}" if query else url


def _selector_params(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "bank_version": config.get("bank_version"),
        "country_id": config.get("country_id"),
        "subject_id": config.get("subject_id"),
    }


def question_detail_url(config: dict[str, Any]) -> str:
    """Detail endpoint for fetching one question by id."""
    return _endpoint(
        str(config.get("base_url") or ""), "/question/detail", _selector_params(config)
    )


def question_list_url(config: dict[str, Any]) -> str:
    """List endpoint for expanding one knowledge code into questions."""
    params = _selector_params(config)
    params["page_size"] = config.get("page_size")
    return _endpoint(str(config.get("base_url") or ""), "/question/list", params)


def knowledge_url(config: dict[str, Any]) -> str:
    """Knowledge endpoint for resolving a knowledge video source."""
    return _endpoint(
        str(config.get("base_url") or ""), "/knowledge/detail", _selector_params(config)
    )
