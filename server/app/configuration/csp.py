"""Document CSP instance switch (#989), env-only like ``server.cors``.

``server.csp.script_unsafe_inline`` (env
``AGENT_LEGION_CSP_SCRIPT_UNSAFE_INLINE``) rolls the document policy's
``script-src`` back to the pre-#989 ``'self' 'unsafe-inline'``: the escape
hatch for instances whose published preview panels still rely on inline
event-handler attributes (``onclick=``) or ``javascript:`` URLs, which the
default nonce policy blocks. Default off = strict; policy rationale in
server/app/http_csp.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CspSettings:
    script_unsafe_inline: bool = False


def load_csp_settings(config: dict[str, Any]) -> CspSettings:
    server_config = config.get("server", {})
    if not isinstance(server_config, dict):
        raise ValueError("server config must be a mapping")
    csp_config = server_config.get("csp", {})
    if not isinstance(csp_config, dict):
        raise ValueError("server.csp config must be a mapping")
    script_unsafe_inline = csp_config.get("script_unsafe_inline", False)
    if not isinstance(script_unsafe_inline, bool):
        raise ValueError("server.csp.script_unsafe_inline must be a boolean")
    return CspSettings(script_unsafe_inline=script_unsafe_inline)
