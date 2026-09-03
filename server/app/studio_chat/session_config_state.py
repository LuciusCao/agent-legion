"""Agent session config state (#368): capture shape and mirror rewrites.

Split from session_config.py (file budget): this half owns the session/new
(or session/load) capture shape (``OpenedAcpSession``), the on_ready row
fields, and the notification-driven mirror rewrites; the switching half
(whitelist + forward) stays in session_config.py. The mirror doubles as the
switching whitelist's data source, so the notification path validates too:
a currentModeId outside the advertised list is logged and dropped, never
persisted (PR #393 review) — a drifted mirror would silently poison every
later whitelist decision.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend

logger = logging.getLogger(__name__)


class OpenedAcpSession(NamedTuple):
    """Result of ACP session establishment, including the agent-advertised
    session config surface. ``modes``/``config_options`` are plain JSON-able
    dicts (camelCase wire aliases); None means the agent did not advertise
    the capability."""

    acp_session_id: str
    loaded_existing: bool
    modes: dict[str, Any] | None
    config_options: list[dict[str, Any]] | None


def opened_session(session_id: str, loaded: bool, response: Any) -> OpenedAcpSession:
    """Serialize a session/new or session/load response; the resume path takes
    the LOAD response's values — the agent's current truth — never a stale
    persisted mirror."""
    modes = getattr(response, "modes", None)
    config_options = getattr(response, "config_options", None)
    return OpenedAcpSession(
        session_id,
        loaded,
        modes.model_dump(by_alias=True, exclude_none=True, mode="json")
        if modes is not None
        else None,
        [
            option.model_dump(by_alias=True, exclude_none=True, mode="json")
            for option in config_options
        ]
        if config_options is not None
        else None,
    )


def session_config_fields(capabilities: dict[str, Any], opened: OpenedAcpSession) -> dict[str, Any]:
    """The on_ready session-row fields for the opened session's config surface.

    capability_snapshot notes whether the agent advertised the two surfaces
    (for UI degradation); the state itself lives in its own columns. The
    resume path overwrites both with the fresh process's values — a stale
    mirror of a dead process says nothing.
    """
    return {
        "acp_session_id": opened.acp_session_id,
        "capability_snapshot": {
            **capabilities,
            "sessionModes": opened.modes is not None,
            "sessionConfigOptions": opened.config_options is not None,
        },
        "session_modes": opened.modes,
        "config_options": opened.config_options,
    }


def apply_config_update(backend: ServiceBackend, session_id: str, update: dict[str, Any]) -> bool:
    """Fold a current_mode_update / config_option_update notification into the
    session's mirrors; False when the update is neither kind. Every accepted
    rewrite bumps the runtime's config_version so an in-flight switch's
    stale write-back yields to this newer agent truth (session_config.py)."""
    kind = update.get("sessionUpdate")
    if kind == "current_mode_update":
        _apply_current_mode(backend, session_id, str(update.get("currentModeId") or ""))
        return True
    if kind == "config_option_update":
        # The notification carries the FULL config state: a model switch
        # shifts the supported thought levels, so the whole list replaces
        # the mirror — never a per-entry patch.
        config_options = update.get("configOptions")
        if isinstance(config_options, list):
            _bump_config_version(backend, session_id)
            backend.db.update_studio_chat_session(session_id, config_options=config_options)
            backend.store.publish_session(session_id)
        else:
            # SDK validation guarantees the shape today; log (never silently
            # freeze the mirror) so an SDK schema drift stays observable.
            logger.warning(
                "studio chat session %s: config_option_update without a configOptions list: %r",
                session_id,
                update,
            )
        return True
    return False


def _apply_current_mode(backend: ServiceBackend, session_id: str, mode_id: str) -> None:
    if not mode_id:
        return
    session = backend.db.get_studio_chat_session(session_id)
    modes = (session or {}).get("session_modes")
    if not isinstance(modes, dict):
        # No advertised mode state to update — a notification for a surface
        # the agent never advertised is dropped, not invented.
        return
    advertised = {str(mode.get("id")) for mode in modes.get("availableModes") or []}
    if mode_id not in advertised:
        logger.warning(
            "studio chat session %s: current_mode_update outside the advertised"
            " mode list, dropped: %s not in %s",
            session_id,
            mode_id,
            sorted(advertised),
        )
        return
    _bump_config_version(backend, session_id)
    backend.db.update_studio_chat_session(
        session_id, session_modes={**modes, "currentModeId": mode_id}
    )
    backend.store.publish_session(session_id)


def _bump_config_version(backend: ServiceBackend, session_id: str) -> None:
    runtime = backend.runtime(session_id)
    if runtime is not None:
        with runtime.lock:
            runtime.config_version += 1
