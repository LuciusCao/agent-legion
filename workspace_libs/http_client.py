"""HTTP client primitives for workflow code nodes (framework layer).

Generic building blocks for nodes that talk to an external HTTP service:
bearer-token headers, a GET-JSON helper with auth-failure classification,
in-band error-payload detection, a "URL not configured" guard, an SSRF
download-URL guard, and a streaming file download. Everything is
parameterized by a ``service`` label and an ``error_type`` so the node keeps
its own business error class (and its failure-classification semantics) while
the mechanics live here once.

Auth-failure semantics: only auth-semantics failures (HTTP 401/403, or an
in-band error code the node declares as an auth code) carry
``auth_failure=True`` — just those justify the node calling
``NodeContext.report_auth_failure`` so the parent executor invalidates the
cached connection token. Transport failures (5xx/timeout/DNS) and non-auth
in-band errors leave the healthy token alone.

Layering rule: standard library + ``requests`` only (requests is in the
worker/sandbox import closure allowlist); never import ``server.app.*``.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class HttpServiceError(RuntimeError):
    """External HTTP service request/response failure.

    Nodes subclass this to keep their business error name (failure
    classification matches on exception class names) and pass the subclass
    as ``error_type`` to the helpers below.
    """

    def __init__(self, message: str, *, auth_failure: bool = False) -> None:
        super().__init__(message)
        self.auth_failure = auth_failure


def bearer_headers(token: str | None) -> dict[str, str]:
    """Accept */* plus a Bearer Authorization header when a token is present."""
    headers: dict[str, str] = {"Accept": "*/*"}
    if token:
        token = token.strip()
        if not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        headers["Authorization"] = token
    return headers


def config_token(config: dict[str, Any]) -> str | None:
    """Dispatch-resolved token from the merged service config, if present.

    Token generation/caching lives in the instance-level connection layer and
    the dispatch injects the resolved plaintext into ``connection_config``;
    legacy frozen payloads still work because their vault-resolved node
    ``token`` is part of the merged config.
    """
    token = str(config.get("token") or "").strip()
    return token or None


def require_configured_url(
    url: str | None,
    *,
    service: str,
    resource: str,
    error_type: type[HttpServiceError] = HttpServiceError,
) -> str:
    """Return the configured endpoint URL or fail with configuration guidance.

    There is no built-in fallback host: the URL comes from the external
    connection config (admin settings) or a node/workspace override.
    """
    stripped = str(url or "").strip()
    if stripped:
        return stripped
    raise error_type(
        f"{service} {resource} URL is not configured: set base_url/api_url on the "
        "external connection (admin settings → 外部服务连接)"
    )


def fetch_json(
    url: str,
    params: dict[str, Any],
    token: str | None,
    *,
    service: str,
    error_type: type[HttpServiceError] = HttpServiceError,
    timeout: int = 15,
) -> dict:
    """GET *url* with a bearer token and return the JSON payload.

    HTTP 401/403 flag ``auth_failure``; other transport/parse failures do
    not. Raises ``error_type`` in every failure case.
    """
    try:
        resp = requests.get(url, params=params, headers=bearer_headers(token), timeout=timeout)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        raise error_type(
            f"{service} request failed: {exc}", auth_failure=status in (401, 403)
        ) from exc
    except (requests.RequestException, ValueError) as exc:
        raise error_type(f"{service} request failed: {exc}") from exc


def check_in_band_error(
    payload: Any,
    resource: str,
    *,
    auth_codes: frozenset[int] | frozenset[str] | set[int],
    service: str,
    error_type: type[HttpServiceError] = HttpServiceError,
) -> None:
    """Raise ``error_type`` on an in-band error payload (non-zero ``code``).

    Success is ``code: 0`` (a missing key is tolerated for legacy payloads);
    any other code fails the call with the code in the message. Codes in
    *auth_codes* flag ``auth_failure`` so nodes invalidate the cached
    connection token; other codes (parameter errors) do not.
    """
    if not isinstance(payload, dict):
        return
    code = payload.get("code")
    # Service contracts use int; str() coercion guards a hypothetical "0".
    if code is None or str(code).strip() == "0":
        return
    try:
        auth_failure = int(code) in auth_codes
    except (TypeError, ValueError):
        auth_failure = False
    message = payload.get("message") or ""
    raise error_type(
        f"{service} 返回错误: code={code} message={message} ({resource})",
        auth_failure=auth_failure,
    )


_ALLOWED_SCHEMES = {"http", "https"}


def validate_download_url(url: str) -> None:
    """SSRF guard for user-influenced download URLs (scheme/host/IP checks)."""
    if not url:
        raise ValueError("Invalid URL: empty")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("Invalid URL: missing hostname")
    hostname_lower = hostname.lower()
    if hostname_lower in {"localhost", "0.0.0.0"}:
        raise ValueError(f"Invalid URL: blocked host {hostname}")
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Reject non-standard IP notations (octal/hex) that ipaddress doesn't
        # parse but underlying getaddrinfo may resolve to internal addresses.
        if all(c in "0123456789abcdefABCDEF.xX" for c in hostname):
            raise ValueError(f"Invalid URL: blocked IP-like host {hostname}") from None
        return
    if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
        raise ValueError(f"Invalid URL: blocked IP {hostname}")


def download_file(
    url: str,
    output_path: Path,
    *,
    allowed_content_type_prefixes: tuple[str, ...] = ("video/", "application/octet-stream"),
    expected: str = "video",
    timeout: int = 120,
    chunk_size: int = 1024 * 1024,
) -> None:
    """Stream *url* to *output_path* with an SSRF guard and content-type gate.

    An existing non-empty output short-circuits (retry-safe); a failed
    download removes the partial file so a retry never sees a truncated
    artifact.
    """
    validate_download_url(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 0:
        return
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if content_type and not any(
            content_type.startswith(prefix) for prefix in allowed_content_type_prefixes
        ):
            raise ValueError(f"Expected {expected} content, got {content_type}")
        try:
            with output_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        handle.write(chunk)
        except Exception:
            if output_path.exists():
                output_path.unlink()
            raise
