from __future__ import annotations

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


class TokenGenConfig(BaseModel):
    app_id: str | None = None
    nonce: str | None = None
    secret: str | None = None
    url: str | None = None


class UserAuthConfig(BaseModel):
    """User-login JWT flow: /user/login -> user_token -> /v1/auth -> JWT."""

    app_id: int | None = None
    account_type: int | None = None
    uname: str | None = None
    password: str | None = None
    client_params: str | None = None
    login_url: str | None = None
    login_resolve_ip: str | None = None
    auth_url: str | None = None


class Config(BaseModel):
    api_base_url: str
    db_path: str
    question_source: QuestionSourceConfig
    token: str | None = None
    token_gen: TokenGenConfig | None = None
    user_auth: UserAuthConfig | None = None
    upload_on_duplicate: Literal["update", "skip"] = "update"
    request_timeout: int = Field(default=30, ge=1)
    max_retries: int = Field(default=3, ge=0)
