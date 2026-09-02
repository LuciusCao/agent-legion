"""Agent session config switching: session/set_mode + session/set_config_option (#368).

Split from service.py (file budget; the capture/mirror half lives in
session_config_state.py). The server-side whitelist is the RCE-guard
principle extended to the agent boundary: a user-supplied modeId/configId/
value never crosses to the agent subprocess unless the session's CURRENT
advertised state (the session-row mirror kept fresh by on_ready and the
update notifications) contains it verbatim.

Concurrency (PR #393 review): the whole validate→forward→write-back span
holds the runtime's config_lock, so two concurrent switches on one session
serialize instead of racing their mirror writes. Notifications never wait on
that lock — they bump runtime.config_version instead, and a switch whose
in-flight RPC was overtaken by a notification skips its own (older) write-back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import ConflictError, InvalidOperationError

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService


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
    if mode_id not in {str(mode.get("id")) for mode in modes["availableModes"]}:
        raise InvalidOperationError(f"Unknown session mode: {mode_id}")
    runtime = _live_runtime(service, session)
    with runtime.config_lock:
        version = _config_version(runtime)
        _forward(runtime.handle.set_session_mode, mode_id)
        # Mirror the accepted value; the agent's own current_mode_update
        # notification (if any) is an idempotent re-write — and when one
        # already landed mid-flight (version moved), the agent's truth is
        # newer than this request's snapshot: skip the stale write-back.
        if _config_version(runtime) == version:
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
    if value not in _select_values(entry):
        raise InvalidOperationError(f"Unknown value for config option '{config_id}': {value}")
    runtime = _live_runtime(service, session)
    with runtime.config_lock:
        version = _config_version(runtime)
        # The response's full config state IS the mirror: configOptions is a
        # required response field (an agent omitting it fails SDK validation
        # and surfaces through _forward as a 409), so no local fold-in
        # fallback — unless a notification overtook the RPC (version moved),
        # in which case the notification's state is newer and wins.
        full_state = _forward(runtime.handle.set_config_option, config_id, value)
        if _config_version(runtime) == version:
            service.db.update_studio_chat_session(session_id, config_options=full_state)
            service.store.publish_session(session_id)
    return service.get_session(session_id)


def _select_values(entry: dict[str, Any]) -> set[str]:
    """Whitelist values of one select entry; ``options`` is either a flat
    value list or a group list (protocol union) — grouped entries contribute
    their nested values, never a bogus top-level one (PR #393 review)."""
    values: set[str] = set()
    for option in entry.get("options") or []:
        if not isinstance(option, dict):
            continue
        nested = option.get("options") if "group" in option else None
        if isinstance(nested, list):
            values |= {str(item.get("value")) for item in nested if isinstance(item, dict)}
        else:
            values.add(str(option.get("value")))
    return values


def _config_version(runtime: SessionRuntime) -> int:
    with runtime.lock:
        return runtime.config_version


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
