"""#748 crash-evidence plumbing for the agent-stderr tail.

Owns the three faces of the rescued stderr tail so ``prepare.py`` stays a
thin caller: the idempotency anchor (read back the scan-time sink file when
a re-run of prepare finds the events file already compressed — the
direct-upload fallback and worker-restart restore both re-enter prepare,
and a second scan of a rewritten file yields nothing), the error_message
summary (exit code + the tail's LAST line — a crash header ends the
stream), and the outbound redaction pass (#748 review P2: the agent env
carries instance secrets, e.g. LLM_GATEWAY_TOKEN; a crash echo quoting them
must never reach error_message / result metadata / structured events).
"""

from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path

from shared.pi_events import STDERR_TAIL_BYTES

# Run-dir member carrying the retained agent-stderr tail. The whole run dir
# ships in the result archive, so the Host-side job dir keeps the evidence
# beside the promoted events.jsonl.
AGENT_STDERR_FILENAME = "agent-stderr.log"

# Secret-shaped literals redacted on top of the env-value pass: provider API
# key prefixes (OpenAI/Anthropic style) and JWTs. Longer alternatives first —
# regex alternation is ordered.
_SECRET_SHAPES = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"
)
# Bearer credentials: keep the scheme word (the crash summary stays readable
# — "Bearer ***" over a bare "***") and redact the credential itself.
_BEARER_SHAPE = re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/-]{16,}")

# Env-var name markers whose VALUES are secrets worth a literal replacement.
_SECRET_NAME_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")

_REDACTED = "***"


def redact_secrets(text: str) -> str:
    """Redact secret material from outbound text (best-effort, never raises).

    Two passes: (1) literal replacement of this process's own secret env
    values (the exact strings the agent env actually carried — names matching
    TOKEN/KEY/SECRET/PASSWORD/CREDENTIAL), (2) secret-shaped literals for
    secrets that did not come from this process (e.g. provider keys echoed
    from a child's own config). Values too short to be secrets (<= 8 chars)
    are skipped: replacing short literals mangles ordinary text for zero
    secrecy gain."""
    for name, value in os.environ.items():
        if len(value) > 8 and any(marker in name.upper() for marker in _SECRET_NAME_MARKERS):
            text = text.replace(value, _REDACTED)
    text = _BEARER_SHAPE.sub(r"\g<1>" + _REDACTED, text)
    return _SECRET_SHAPES.sub(_REDACTED, text)


def stderr_tail_for_run(run_dir: Path, scanned_tail: bytes) -> bytes:
    """The idempotent stderr tail for one prepare pass (#748 review P1).

    ``scanned_tail`` is the fresh scan's capture; when it is empty but the
    scan-time sink file exists (a previous pass already rescued and
    compressed the events — direct-upload fallback, worker-restart restore),
    the file IS the tail: the compression rewrite destroyed the raw lines,
    so nothing else can recover them. Returns redacted bytes AND rewrites
    the sink file in place with them (the scan writes the RAW tail — shared/
    is stdlib-only and cannot redact — while the file is the outbound
    archive face: it ships in the result tar and anchors every later
    re-entry, so the on-disk anchor must never hold a secret echo; a
    rewrite failure degrades to the raw file, never fails prepare)."""
    tail = scanned_tail
    if not tail:
        sink = run_dir / AGENT_STDERR_FILENAME
        if sink.is_file():
            tail = sink.read_bytes()[:STDERR_TAIL_BYTES]
    if not tail:
        return b""
    redacted = redact_secrets(tail.decode("utf-8", "replace")).encode("utf-8")[:STDERR_TAIL_BYTES]
    if redacted != tail:
        with suppress(OSError):
            (run_dir / AGENT_STDERR_FILENAME).write_bytes(redacted)
    return redacted


def stderr_error_message(exit_code: int, stderr_tail: bytes) -> str:
    """error_message for a crashed agent process — exit code plus the
    retained stderr tail's LAST line (the crash header: a panic/trace ends
    the stream, so the newest — and most explanatory — line is the last one;
    the external API's error_summary truncates at 240 chars). The full
    multi-line tail rides the archive member + metadata; the empty tail
    keeps the legacy message unchanged."""
    summary = stderr_tail.decode("utf-8", "replace").strip()
    if not summary:
        return f"Agent process exited {exit_code}"
    last_line = " ".join(summary.splitlines()[-1].split())
    return f"Agent process exited {exit_code}: {redact_secrets(last_line[:200])}"
