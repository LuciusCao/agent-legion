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

from typing import Any

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
    not. Raises ``error_type`` in every failure case. Redirects are refused
    (``allow_redirects=False`` + explicit 3xx rejection): following them
    would bypass any caller-side URL guard by hopping to an internal
    address. A service that legitimately redirects needs an explicit design,
    not a silent follow.
    """
    try:
        # Note for node authors: request exceptions embed the full URL
        # *including the query string*, so never put secrets in ``params`` —
        # they would leak into error messages and from there into job logs.
        resp = requests.get(
            url,
            params=params,
            headers=bearer_headers(token),
            timeout=timeout,
            allow_redirects=False,
        )
        if 300 <= resp.status_code < 400:
            raise error_type(
                f"{service} request failed: unexpected redirect (HTTP {resp.status_code})"
            )
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
