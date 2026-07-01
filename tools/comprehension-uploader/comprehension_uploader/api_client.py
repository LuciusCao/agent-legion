from __future__ import annotations

from typing import Any

import requests

from comprehension_uploader.config import Config
from comprehension_uploader.package_parser import UploadRecord


class ComprehensionAPIClient:
    """Thin HTTP client for the comprehension info endpoints."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        token = config.auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.session.headers.update(headers)

    def _url(self, path: str) -> str:
        return f"{self.config.api_base_url.rstrip('/')}{path}"

    def add(self, record: UploadRecord, fingerprint: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subject_id": record.subject_id,
            "fingerprint": fingerprint,
            "question_uuid": record.question_uuid,
            "question_vno": record.question_vno,
            "comprehension_difficulty": record.comprehension_difficulty,
            "format_vno": record.format_vno,
            "comprehension_data": record.comprehension_data,
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        response = self.session.post(
            self._url("/v1/addComprehensionInfo"),
            json=payload,
            timeout=self.config.request_timeout,
        )
        response.raise_for_status()
        return response.json()

    def update(
        self, record: UploadRecord, fingerprint: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {"fingerprint": fingerprint, **fields}
        response = self.session.post(
            self._url("/v1/updateComprehensionInfo"),
            json=payload,
            timeout=self.config.request_timeout,
        )
        response.raise_for_status()
        return response.json()
