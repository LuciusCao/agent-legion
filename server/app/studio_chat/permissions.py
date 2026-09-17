"""Permission policy for Studio chat sessions (split from service.py, file budget).

Decision order for an ACP permission request:
1. agent-legion MCP tool calls auto-approve ONLY when the rawInput passes the
   matched tool's registered input schema (tool_schemas.py) — the session's
   workspace-bound scoped token is already the authority boundary, and a
   schema-valid payload proves the call really is that tool call rather than
   a forged title (#687 attack fix, fail-closed: unknown tool / missing or
   failing schema / malformed rawInput parks for a human instead);
2. local read-only ACP kinds (``read`` / ``search`` — the Read/Glob/Grep
   class) auto-approve as side-effect-free, but only when rawInput is a
   non-empty object whose every key is a verified read-only observation key
   (see READ_ONLY_INPUT_FIELDS) — the kind field is agent-reported free
   text, so a ``read`` request without rawInput evidence (kimi 0.42.0
   permission requests carry only title) or with write/execute-shaped keys
   must park (#687 round 2);
3. the per-session allow-all switch approves everything else without a
   roundtrip (a deliberate, human-flipped switch);
4. otherwise the request parks for a human answer, and an unanswered prompt
   (browser closed, tab abandoned) is auto-denied after the timeout instead
   of parking the ACP thread-pool thread and the agent subprocess forever.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from server.app.studio_chat.payloads import pick_allow_option
from server.app.studio_chat.runtime import PendingPermission

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend

logger = logging.getLogger(__name__)

PERMISSION_TIMEOUT_SECONDS = 120

# ACP ToolKind values that are local and read-only (the Read/Glob/Grep class).
# Write/execute kinds (edit, delete, move, execute, fetch, ...) still require
# human confirmation; a false negative only degrades to the human path.
READ_ONLY_TOOL_KINDS = frozenset({"read", "search"})

# Fields that turn a tool call into something the terminal protocol can
# execute. Their presence in a self-reported read-only rawInput is never a
# Read/Glob/Grep shape — it parks for a human (#687 CRITICAL-2: "read" + a
# command must not auto-approve into a Bash run).
EXECUTION_CAPABLE_FIELDS = frozenset({"command", "args", "env", "cwd"})

# Write-semantics keys: the input shape of Edit/Write/Move/Copy tools
# (content is the bytes to write, from/to and source/target are the move/copy
# pair, old_string/new_string the edit pair, name a create-target, position
# a seek-then-write offset, multi_edit a batch-edit switch). Round 1 kept
# them in the read whitelist, so a forged kind=read + Edit/Write-shaped
# rawInput auto-approved with zero human roundtrips (review HIGH-1); they are
# now hard failures of the read gate, kept as a named set so tests can pin
# that the read whitelist and the write keys stay disjoint.
WRITE_SEMANTIC_KEYS = frozenset(
    {
        "from",
        "to",
        "content",
        "name",
        "source",
        "target",
        "destination",
        "position",
        "multi_edit",
        "old_string",
        "new_string",
    }
)

# Verified read-only observation keys — every entry is justified by where it
# is actually sent (kimi 0.42.0 wire forensics / ACP spec), never by "sounds
# harmless". A read-kind rawInput with a key outside this profile is a shape
# we cannot vouch for → confirm (fail-closed). Adding a key here requires a
# source citation, and write-semantics keys are forbidden by construction
# (see the assertion below).
READ_ONLY_INPUT_FIELDS = frozenset(
    {
        # Path selectors (kimi Read/Glob/Grep + ACP spec filePath/filePaths;
        # "path"/"paths" are kimi's Grep/Glob key spellings).
        "file_path",  # kimi Read
        "file_paths",
        "path",  # kimi Grep (search root)
        "paths",
        # Pattern/glob matching (kimi Grep/Glob).
        "pattern",  # kimi Grep
        "regex",
        "glob",  # kimi Glob
        "search",
        # Filter shaping (kimi Grep).
        "include",
        "exclude",
        "include_ignored",  # kimi Glob (ripgrep --no-ignore inversion)
        "type",  # kimi Grep (ripgrep -t type filter)
        # Read pagination (kimi Read: line_offset/n_lines/max_chars were the
        # round-1 miss that parked 35% of real Read calls).
        "line_offset",  # kimi Read
        "n_lines",  # kimi Read
        "max_chars",  # kimi Read
        "offset",
        "limit",
        "max_results",
        # Grep output shaping (kimi sends ripgrep's short flag spellings;
        # the long snake_case spellings cover the ACP reference agent).
        "output_mode",  # kimi Grep (content/files_with_matches/count)
        "head_limit",  # kimi Grep
        "-n",  # kimi Grep (line numbers)
        "-i",  # kimi Grep (case-insensitive)
        "-A",  # kimi Grep (after-context)
        "-B",  # kimi Grep (before-context)
        "-C",  # kimi Grep (context)
        "multiline",  # kimi Grep
        "case_sensitive",
        "context_lines",
        "before_context",
        "after_context",
        "show_line_numbers",
    }
)

# The two key sets must stay disjoint: a key that is both read-shaped and
# write-shaped would make the "no write keys" gate vacuous for it. This runs
# at import time (cheap frozenset intersection) so a future careless edit to
# either set fails loudly instead of silently re-opening HIGH-1. A raise
# guard, not assert — assert is stripped under python -O.
if READ_ONLY_INPUT_FIELDS & WRITE_SEMANTIC_KEYS:
    raise RuntimeError("read-only input fields and write-semantics keys must be disjoint")


def is_read_only_tool_call(tool_call: dict[str, Any]) -> bool:
    """Whether a read-kind tool call is a known auto-approvable read shape.

    Two gates, both fail-closed (#687 CRITICAL-2 + round-2 review): the
    agent-reported kind must be read/search, AND the rawInput must be a
    NON-EMPTY JSON object whose every key is a verified read-only
    observation key — no execution-capable fields (command/args/env/cwd),
    no write-semantics keys (from/to/content/name/source/target/position/
    multi_edit/old_string/new_string), no unrecognized keys.

    A MISSING or non-object rawInput also fails the shape check, and that
    is deliberate fail-closed, not an oversight: without input evidence we
    cannot prove the call is a read-only shape, and the kind field alone is
    agent-authored free text. This is a known product cost — kimi 0.42.0
    permission requests carry only a title (no kind/rawInput), so real kimi
    Read/Grep traffic parks for a human until the agent (or ACP client
    surface) includes the input in the permission request; see the round-2
    report's kimi impact note before loosening this.
    """
    if str(tool_call.get("kind") or "") not in READ_ONLY_TOOL_KINDS:
        return False
    raw_input = tool_call.get("rawInput")
    if not isinstance(raw_input, dict) or not raw_input:
        return False
    return all(
        key in READ_ONLY_INPUT_FIELDS and key not in EXECUTION_CAPABLE_FIELDS for key in raw_input
    )


# Per-process strict rawInput validator for the platform MCP tool surface.
# Built lazily on first use (module import order in entry points stays
# independent of the MCP server package) and shared across sessions/threads;
# construction is idempotent and the instance is read-only afterwards.
_tool_input_validator: Any = None
_tool_input_validator_lock = threading.Lock()


def _get_tool_input_validator() -> Any:
    global _tool_input_validator
    with _tool_input_validator_lock:
        if _tool_input_validator is None:
            from server.app.mcp_server.tool_schemas import ToolInputValidator

            _tool_input_validator = ToolInputValidator()
        return _tool_input_validator


def _matched_agent_legion_tool_call(tool_call: dict[str, Any]) -> str | None:
    """The platform MCP tool name this tool call's identity fields carry.

    Any identity field (title/kind/name) may carry it — the same any() the
    recognition layer uses — but the matched NAME is what the caller binds
    the decision to: rawInput must then satisfy that specific tool's input
    schema before an auto-approve (#687).
    """
    from server.app.studio_chat.prompts import agent_legion_tool_name

    for key in ("title", "kind", "name"):
        value = tool_call.get(key)
        if isinstance(value, str):
            matched = agent_legion_tool_name(value)
            if matched is not None:
                return matched
    return None


def handle_permission_request(
    backend: ServiceBackend,
    session_id: str,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply the permission policy; blocks on the human answer when parked."""
    from server.app.studio_chat.mcp_hint import is_agent_legion_tool_call

    if is_agent_legion_tool_call(tool_call):
        matched_tool = _matched_agent_legion_tool_call(tool_call)
        # Fail-closed (#687 CRITICAL-1): the title claims a platform tool but
        # rawInput does not satisfy that tool's registered input schema (or no
        # schema is available / rawInput is malformed) — park for a human
        # instead of auto-approving a call whose actual input we cannot vouch
        # for. The reason never contains payload text, so it is safe to log.
        # The validator call itself is also fail-closed (round-2 review
        # MEDIUM-3): a validator exception (e.g. a dangling $ref in a future
        # schema) degrades this ONE request to the human path instead of
        # propagating into the ACP callback thread and killing the RPC for
        # every session sharing it.
        try:
            ok, reason = _get_tool_input_validator().validate(
                matched_tool, tool_call.get("rawInput")
            )
        except Exception as exc:  # #204 broad-except audit: jsonschema's dangling-$ref probe raises _WrappedReferencingError (a referencing.exceptions.Unresolvable) out of iter_errors; any other unexpected validator failure equally means "cannot vouch for this input" — degrade to the human path (park) with the type name in the reason, never swallow-and-forget: it is logged with the tool name just below.
            ok, reason = False, f"validation_error: validator raised {type(exc).__name__}"
        if ok:
            runtime = backend.runtime(session_id)
            if runtime is not None:
                with runtime.lock:
                    runtime.mcp_observed = True
            backend.store.mark_mcp_verified(session_id)
            return auto_approve(backend, session_id, tool_call, options, decision="auto_approved")
        logger.warning(
            "studio chat MCP auto-approve rejected for session %s (tool %r): %s",
            session_id,
            matched_tool,
            reason,
        )
    if is_read_only_tool_call(tool_call):
        return auto_approve(backend, session_id, tool_call, options, decision="auto_read_only")
    session = backend.db.get_studio_chat_session(session_id) or {}
    if session.get("allow_all_permissions"):
        return auto_approve(backend, session_id, tool_call, options, decision="allow_all")
    request_id = uuid4().hex
    pending = PendingPermission(request_id)
    runtime = backend.runtime(session_id)
    if runtime is None:
        return {"deny": True}
    with runtime.lock:
        # Teardown flips `closed` under this same lock before its settle
        # sweep; parking after that point would hang until the timeout (#158).
        if runtime.closed:
            return {"deny": True}
        runtime.pending_permissions[request_id] = pending
    backend.store.append_message(
        session_id,
        "permission",
        "agent",
        {
            "request_id": request_id,
            "status": "pending",
            "tool_call": tool_call,
            "options": options,
        },
    )
    # Atomic check-and-set (#158): an unconditional write could overwrite a
    # concurrent close/error back to a live state. 'awaiting_permission' is an
    # allowed current state because concurrent prompts of the same turn
    # re-park. When the guard fails the session is closing or dead: deny at
    # once instead of parking against a torn-down runtime.
    parked = backend.db.update_studio_chat_session_if(
        session_id,
        status_in=("running", "awaiting_permission"),
        status="awaiting_permission",
    )
    if not parked:
        with runtime.lock:
            runtime.pending_permissions.pop(request_id, None)
        pending.decision = {"deny": True, "via": "session_closed"}
    else:
        backend.store.publish_session(session_id)
        try:
            settled = pending.event.wait(timeout=PERMISSION_TIMEOUT_SECONDS)
            if not settled:
                logger.warning("studio chat permission %s timed out; auto-denied", request_id)
                with runtime.lock:
                    # Dict membership is the not-yet-settled criterion (#158):
                    # a human answer that raced the timeout already popped the
                    # request and owns the decision.
                    orphaned = runtime.pending_permissions.pop(request_id, None)
                    if orphaned is not None:
                        orphaned.decision = {"deny": True, "via": "timeout"}
        finally:
            with runtime.lock:
                runtime.pending_permissions.pop(request_id, None)
                still_parked = bool(runtime.pending_permissions)
            # Only the awaiting_permission → running transition is ours, and
            # only once no prompt of this turn is still parked: a close (or
            # fatal error) that settled this waiter must not be overwritten
            # back to running (ghost live session, #158).
            if not still_parked and backend.db.update_studio_chat_session_if(
                session_id, status_in=("awaiting_permission",), status="running"
            ):
                backend.store.publish_session(session_id)
    decision = pending.decision
    backend.store.append_message(
        session_id,
        "permission",
        "user",
        {"request_id": request_id, "status": "resolved", "decision": decision},
    )
    return decision


def auto_approve(
    backend: ServiceBackend,
    session_id: str,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    decision: str,
) -> dict[str, Any]:
    option = pick_allow_option(options)
    if option is None:
        outcome: dict[str, Any] = {"deny": True}
    else:
        outcome = {"option_id": option["optionId"]}
    backend.store.append_message(
        session_id,
        "permission",
        "system",
        {
            "status": "resolved",
            "decision": {**outcome, "via": decision},
            "tool_call": tool_call,
        },
    )
    return outcome
