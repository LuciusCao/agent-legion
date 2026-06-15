import os
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_KNOWLEDGE_URL = (
    "http://cms.internal.example.com/v2/knowledge/detail?bank_version=v5&country_id=1&subject_id=2"
)
DEFAULT_QUESTION_URL = (
    "http://cms.internal.example.com/v2/question/detail?bank_version=v5&country_id=1&subject_id=2"
)
DEFAULT_QUESTION_LIST_URL = (
    "http://cms.internal.example.com/v2/question/list?bank_version=v5&country_id=1&subject_id=2"
)


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
    return resp.json()  # type: ignore[no-any-return]


@dataclass(frozen=True)
class CmsVideoLookup:
    status: str
    url: str = ""
    title: str = ""
    source_uuid: str = ""
    payload: dict[str, Any] | None = None


def get_token(env: str, config: dict[str, Any] | None = None) -> str | None:
    from server.app.cms.auth import _generate_prod_token

    config = config or {}
    token = os.environ.get("BASECMS_TOKEN")
    if token:
        return token
    token = config.get("token")
    if token:
        result: str = str(token)
        return result
    if env == "prod":
        return _generate_prod_token(config)
    return None
