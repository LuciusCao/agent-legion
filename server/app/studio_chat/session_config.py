"""Agent session config surface (#368): capture, whitelist, switching, mirrors.

Split from service.py / session_load.py / events.py (file budget): the whole
agent-advertised config surface lives here — the session/new (or session/load)
capture shape (``OpenedAcpSession``), the on_ready row fields, the
notification-driven mirror rewrites, and the set_mode / set_config_option
switching. The server-side whitelist is the RCE-guard principle extended to
the agent boundary: a user-supplied modeId/configId/value never crosses to
the agent subprocess unless the session's CURRENT advertised state (the
session-row mirror kept fresh by on_ready and the update notifications)
contains it verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from server.app.services.job_errors import ConflictError, InvalidOperationError

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService


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
    session's mirrors; False when the update is neither kind."""
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
            backend.db.update_studio_chat_session(session_id, config_options=config_options)
            backend.store.publish_session(session_id)
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
    backend.db.update_studio_chat_session(
        session_id, session_modes={**modes, "currentModeId": mode_id}
    )
    backend.store.publish_session(session_id)


def _live_runtime(service: StudioChatService, session: dict[str, Any]) -> SessionRuntime:
    if session["status"] == "closed":
        raise ConflictError("Chat session is closed")
    runtime = service.runtime(str(session["id"]))
    if runtime is None:
        raise ConflictError("Chat session is not running on this server")
    return runtime


def set_session_mode(
    service: StudioChatService, session_id: str, workspace_id: str, mode_id: str
) -> dict[str, Any]:
    """Validate against the advertised mode list, forward, mirror, publish."""
    session = service.get_session(session_id, workspace_id)
    modes = session.get("session_modes")
    if not isinstance(modes, dict) or not modes.get("availableModes"):
        raise InvalidOperationError("This agent does not advertise session modes")
    available = modes["availableModes"]
    if mode_id not in {str(mode.get("id")) for mode in available}:
        raise InvalidOperationError(f"Unknown session mode: {mode_id}")
    runtime = _live_runtime(service, session)
    _forward(runtime.handle.set_session_mode, mode_id)
    # Mirror the accepted value immediately; the agent's own
    # current_mode_update notification (if any) is an idempotent re-write.
    service.db.update_studio_chat_session(
        session_id, session_modes={**modes, "currentModeId": mode_id}
    )
    service.store.publish_session(session_id)
    return service.get_session(session_id)


def set_session_config_option(
    service: StudioChatService, session_id: str, workspace_id: str, config_id: str, value: str
) -> dict[str, Any]:
    """Validate against the advertised option list, forward, mirror, publish."""
    session = service.get_session(session_id, workspace_id)
    config_options = session.get("config_options")
    if not isinstance(config_options, list):
        raise InvalidOperationError("This agent does not advertise config options")
    entry = next(
        (
            option
            for option in config_options
            if isinstance(option, dict) and str(option.get("id")) == config_id
        ),
        None,
    )
    if entry is None:
        raise InvalidOperationError(f"Unknown config option: {config_id}")
    if entry.get("type") != "select":
        # boolean options are not declared in ClientCapabilities (one-phase
        # scope, #368) — an agent offering one anyway is out of contract.
        raise InvalidOperationError(f"Config option '{config_id}' is not a select option")
    values = {str(option.get("value")) for option in entry.get("options") or []}
    if value not in values:
        raise InvalidOperationError(f"Unknown value for config option '{config_id}': {value}")
    runtime = _live_runtime(service, session)
    # The response's full config state IS the mirror: configOptions is a
    # required response field (an agent omitting it fails SDK validation and
    # surfaces through _forward as a 409), so no local fold-in fallback.
    full_state = _forward(runtime.handle.set_config_option, config_id, value)
    service.db.update_studio_chat_session(session_id, config_options=full_state)
    service.store.publish_session(session_id)
    return service.get_session(session_id)


def _forward(call: Any, *args: str) -> Any:
    """Send one set request to the agent; agent refusal / timeout / teardown
    all surface as ConflictError (409) — the session state is the conflict."""
    try:
        return call(*args)
    except Exception as exc:
        # #204 broad-except audit: the outcome space behind a cross-thread
        # JSON-RPC round-trip is genuinely mixed (RequestError refusal,
        # concurrent teardown RuntimeError, FutureTimeoutError) and every one
        # maps to the SAME designed semantics: the switch did not happen and
        # the caller must see why. The original error text crosses into the
        # HTTP detail; nothing is masked.
        raise ConflictError(f"Agent rejected the change: {exc}") from exc
