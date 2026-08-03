"""Message markers and regexes for the failure classification rule table.

The same technical failure reaches ``node_runs.error_message`` in two textual
shapes: Agent Workers report the raw message (e.g. ``terminated``) while the
local Pi runner wraps model errors as ``Pi model call failed: ...``.
"""

from __future__ import annotations

import re

_REVIEW_REJECTED_MARKERS = (
    "review_rejected:",
    "content review rejected by skill",
    "Output validation failed: Review rejected",
)
_PI_MODEL_CALL_PREFIX = "Pi model call failed:"
# velites provider errors (ProviderError::Call / ::Transient) share this prefix;
# "(transient):" variants are infra-side, plain ones are deterministic.
_PROVIDER_CALL_PREFIX = "provider call failed"
_PROVIDER_CONTENT_FILTER_MARKER = "unexpected finish_reason `sensitive`"
_PROVIDER_CALL_STREAM_MARKERS = (
    "stream error",
    "error decoding response body",
    "unexpected EOF",
)
_MISSING_OUTPUTS_PREFIXES = ("missing outputs", "missing required file")
_NO_OUTPUT_ARTIFACTS_PREFIX = "Agent Worker did not report output artifacts"
_UNPACK_FAILURE = "failed to unpack Agent result"
_RESOURCE_LIMIT_MARKERS = ("Too many open files", "No space left on device")
_SQLITE_MARKERS = (
    "database is locked",
    "cannot rollback",
    "unable to open database file",
    "database or disk is full",
)
_EXECUTION_ERROR_MARKERS = (
    "openclaw command failed",
    "SQLite objects created in a thread",
    "isolated handler did not return a result",
    "object has no attribute",
    "[Errno 2] No such file or directory",
)
_NETWORK_MARKERS = ("IncompleteRead", "ChunkedEncodingError", "Connection broken")
_DB_POOL_MARKERS = (
    "PoolTimeout",
    "connection pool exhausted",
    "couldn't get a connection after",
)
_PROCESS_EXITED_RE = re.compile(r"^Agent process exited (\d+)$")
_TERMINATED_WORD_RE = re.compile(r"\bterminated\b")
_EXECUTOR_NOT_REGISTERED_RE = re.compile(r"^Executor '.+' is not registered$")
_INTERACTION_CONTRACT_RE = re.compile(r"^Interaction \d+.*(is missing|has unknown type)")
