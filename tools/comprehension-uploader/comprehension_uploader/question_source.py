from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import requests

from comprehension_uploader.auth import get_token
from comprehension_uploader.config import Config, QuestionSourceConfig


class QuestionSource(Protocol):
    def get_latest(self, question_id: str) -> dict[str, Any] | None:
        """Return the latest stem/options payload for a question, or None."""
        ...


class JSONFileQuestionSource:
    def __init__(self, path: str | Path) -> None:
        raw_path = Path(path)
        with raw_path.open(encoding="utf-8") as handle:
            data = json.load(handle)

        if isinstance(data, dict):
            self._mapping = data
        elif isinstance(data, list):
            self._mapping = {
                item["question_id"]: item
                for item in data
                if isinstance(item, dict) and "question_id" in item
            }
        else:
            self._mapping = {}

    def get_latest(self, question_id: str) -> dict[str, Any] | None:
        result = self._mapping.get(question_id)
        return result if isinstance(result, dict) else None


class HTTPQuestionSource:
    def __init__(self, config: QuestionSourceConfig, token: str | None) -> None:
        self.url_template = config.url_template or ""
        self.headers = (config.headers or {}).copy()
        if token:
            self.headers = {key: value.format(token=token) for key, value in self.headers.items()}
        self.response_path = config.response_path
        self.timeout = 30

    def get_latest(self, question_id: str) -> dict[str, Any] | None:
        url = self.url_template.format(question_id=question_id)
        response = requests.get(url, headers=self.headers, timeout=self.timeout)
        response.raise_for_status()
        data: Any = response.json()
        if self.response_path:
            for part in self.response_path.split("."):
                data = data.get(part, {}) if isinstance(data, dict) else {}
        return data if isinstance(data, dict) else None


def build_question_source(config: Config) -> QuestionSource:
    if config.question_source.type == "json_file":
        return JSONFileQuestionSource(config.question_source.path or "")
    token = get_token(config.model_dump())
    return HTTPQuestionSource(config.question_source, token)
