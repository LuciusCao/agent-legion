"""Per-response script nonce of the document CSP (#989).

Split from http_csp.py (policy rationale there) for the file budget. The
middleware plants one ``_NonceSlot`` per HTTP request; the SPA route that
embeds the nonce in index.html fills it via ``issue_csp_nonce``; the
middleware then renders the header from whatever the slot holds — so only a
document that actually carries the nonce gets one in its policy.
"""

from __future__ import annotations

import secrets

from starlette.types import Scope

# vite ``html.cspNonce`` (frontend/vite.config.ts) writes this literal into
# the built index.html; the SPA route replaces it per response. The same
# literal lives in frontend/src/features/previewPanel/panelCsp.ts.
CSP_NONCE_PLACEHOLDER = "__AGENT_LEGION_CSP_NONCE__"
_NONCE_SCOPE_KEY = "agent_legion.csp_nonce"


class _NonceSlot:
    """Per-request holder; a mutable object (not a scope value) so the nonce
    survives any scope copy between the middleware and the endpoint."""

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value: str | None = None


def plant_nonce_slot(scope: Scope) -> _NonceSlot:
    slot = scope[_NONCE_SCOPE_KEY] = _NonceSlot()
    return slot


def issue_csp_nonce(scope: Scope) -> str:
    """The script nonce of this response (created on first call).

    Without the middleware (no slot) the nonce reaches no header and the
    stamped attributes are inert.
    """
    slot = scope.get(_NONCE_SCOPE_KEY)
    if not isinstance(slot, _NonceSlot):
        return secrets.token_urlsafe(18)
    if slot.value is None:
        slot.value = secrets.token_urlsafe(18)
    return slot.value


def script_src_directive(nonce: str | None, unsafe_inline: bool) -> str:
    """Compat mode drops the nonce: its presence makes browsers ignore
    ``'unsafe-inline'``, which would defeat the instance switch."""
    if unsafe_inline:
        return "script-src 'self' 'unsafe-inline'"
    if nonce:
        return f"script-src 'self' 'nonce-{nonce}'"
    return "script-src 'self'"
