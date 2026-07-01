from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field


class ConfigError(Exception):
    """Raised when the configuration file is invalid."""


class QuestionSourceConfig(BaseModel):
    type: Literal["json_file", "http"]
    path: str | None = None
    url_template: str | None = None
    headers: dict[str, str] | None = None
    response_path: str | None = None


class Config(BaseModel):
    api_base_url: str
    auth_token_env: str
    db_path: str
    question_source: QuestionSourceConfig
    upload_on_duplicate: Literal["update", "skip"] = "update"
    request_timeout: int = Field(default=30, ge=1)
    max_retries: int = Field(default=3, ge=0)

    def auth_token(self) -> str:
        token = os.environ.get(self.auth_token_env)
        if not token:
            raise ConfigError(
                f"Authentication token environment variable {self.auth_token_env!r} is not set"
            )
        return token
