"""Credential configuration for the remote LLM gateway."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def load_pi_provider(models_json: Path, provider: str) -> tuple[str, str]:
    """Load an upstream URL and credential from Pi's models.json."""
    path = models_json.expanduser()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        config = document["providers"][provider]
        upstream = config["baseUrl"].rstrip("/")
        key = config["apiKey"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"cannot load provider {provider!r} from {path}") from exc

    if upstream.endswith("/v1"):
        upstream = upstream[:-3]
    if not upstream or not key:
        raise ValueError(f"provider {provider!r} in {path} needs baseUrl and apiKey")
    return upstream, key


def add_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", help="Pi provider to load when env credentials are absent")
    parser.add_argument(
        "--models-json",
        type=Path,
        default=Path("~/.pi/agent/models.json"),
        help="Pi models.json path (default: ~/.pi/agent/models.json)",
    )


def resolve_credentials(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> tuple[str, str]:
    upstream = os.environ.get("REMOTE_LLM_UPSTREAM", "")
    key = os.environ.get("REMOTE_LLM_KEY", "")
    if (not upstream or not key) and args.provider:
        try:
            file_upstream, file_key = load_pi_provider(args.models_json, args.provider)
        except ValueError as exc:
            parser.error(str(exc))
        upstream = upstream or file_upstream
        key = key or file_key
    if not upstream:
        parser.error("REMOTE_LLM_UPSTREAM or --provider is required (中台 base URL)")
    if not key:
        parser.error("REMOTE_LLM_KEY or --provider is required (中台 credential)")
    return upstream, key
