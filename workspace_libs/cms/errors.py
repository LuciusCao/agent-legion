"""CMS error type and in-band error-code detection (question/video knowledge pack).

The CMS signals auth/parameter failures in-band: HTTP 200 with a non-zero
``code`` and ``data: null``. This module owns the shared error type and the
payload check so every endpoint (detail/list/knowledge) classifies failures
the same way.
"""

from typing import Any


class CmsClientError(RuntimeError):
    """CMS request/response failure.

    ``auth_failure`` marks auth-semantics failures only (HTTP 401/403 or a
    known in-band auth error code): just those justify invalidating the
    cached connection token via ``NodeContext.report_auth_failure`` (the
    node SDK marker channel). Transport failures (5xx/timeout/DNS) and
    non-auth in-band errors (parameter errors) leave the healthy token alone.
    """

    def __init__(self, message: str, *, auth_failure: bool = False) -> None:
        super().__init__(message)
        self.auth_failure = auth_failure


# Observed CMS in-band auth codes: 10015 is JWT 验证失败. Extend as new
# auth codes are observed; non-auth codes (parameter errors) must not be
# added here, they do not justify invalidating the cached token.
_IN_BAND_AUTH_CODES = frozenset({10015})


def check_in_band_error(payload: Any, resource: str) -> None:
    """Raise CmsClientError on an in-band CMS error payload (non-zero ``code``).

    Success is ``code: 0`` (a missing key is tolerated for legacy payloads);
    any other code fails the call with the code in the message. Known auth
    codes flag ``auth_failure`` so nodes invalidate the cached connection
    token; other codes (parameter errors) do not.
    """
    if not isinstance(payload, dict):
        return
    code = payload.get("code")
    # CMS contract is int; str() coercion guards a hypothetical "0".
    if code is None or str(code).strip() == "0":
        return
    try:
        auth_failure = int(code) in _IN_BAND_AUTH_CODES
    except (TypeError, ValueError):
        auth_failure = False
    message = payload.get("message") or ""
    raise CmsClientError(
        f"CMS 返回错误: code={code} message={message} ({resource})",
        auth_failure=auth_failure,
    )
