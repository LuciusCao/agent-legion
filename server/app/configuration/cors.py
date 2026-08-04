from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CorsSettings:
    allow_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    allow_credentials: bool = False


def load_cors_settings(config: dict[str, Any]) -> CorsSettings:
    server_config = config.get("server", {})
    if not isinstance(server_config, dict):
        raise ValueError("server config must be a mapping")
    cors_config = server_config.get("cors", {})
    if not isinstance(cors_config, dict):
        raise ValueError("server.cors config must be a mapping")

    raw_origins = cors_config.get("allow_origins", CorsSettings().allow_origins)
    if not isinstance(raw_origins, (list, tuple)) or not all(
        isinstance(origin, str) and origin.strip() for origin in raw_origins
    ):
        raise ValueError("server.cors.allow_origins must be a list of non-empty strings")
    allow_origins = tuple(origin.strip().rstrip("/") for origin in raw_origins)
    allow_credentials = cors_config.get("allow_credentials", False)
    if not isinstance(allow_credentials, bool):
        raise ValueError("server.cors.allow_credentials must be a boolean")
    if allow_credentials and "*" in allow_origins:
        raise ValueError("credentialed CORS cannot use a wildcard origin")
    return CorsSettings(
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
    )
