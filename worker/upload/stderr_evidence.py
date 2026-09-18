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

Redaction boundary (best-effort, deliberately): the literal pass covers
THIS process's secret-named env values plus the worker config's
``environment:`` block (register_secrets, #748 R2 P2-3); the shape pass
covers provider key prefixes (sk-/sk-ant-/ghp_/gho_) and Slack bot/user/app
tokens (``xox[bap]/``) plus JWTs and Bearer credentials. NOT covered: env
values shorter than the byte threshold, secret-named values from OTHER
machines not echoed through this process, and custom gateway tokens with
no recognizable shape — a custom-token echo in stderr survives redaction
(known best-effort boundary; the sink file is the durable evidence face
and is redacted by the same pass).
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import suppress
from pathlib import Path

from shared.pi_events import STDERR_TAIL_BYTES

# Run-dir member carrying the retained agent-stderr tail. The whole run dir
# ships in the result archive, so the Host-side job dir keeps the evidence
# beside the promoted events.jsonl.
AGENT_STDERR_FILENAME = "agent-stderr.log"

# Secret-shaped literals redacted on top of the env-value pass: provider API
# key prefixes (OpenAI/Anthropic/GitHub) and Slack bot/user/app tokens plus
# JWTs. Longer alternatives first — regex alternation is ordered.
_SECRET_SHAPES = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|gho_[A-Za-z0-9]{20,}"
    r"|xox[bap]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"
)
# Bearer credentials: keep the scheme word (the crash summary stays readable
# — "Bearer ***" over a bare "***") and redact the credential itself.
_BEARER_SHAPE = re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/-]{16,}")

# Env-var name markers whose VALUES are secrets worth a literal replacement.
_SECRET_NAME_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")

# #748 R2 P3-4: the "too short to be a secret" skip uses BYTES, not chars —
# 8 CJK chars are 24 bytes of real key material. Measured on the UTF-8
# encoding, the same metric the outbound faces serialize to.
_MIN_SECRET_BYTES = 8

_REDACTED = "***"

# #748 R2 P2-3: the worker config's ``environment:`` block is the OFFICIAL
# channel that injects secrets into the agent subprocess (executor.py), but
# only ``os.environ`` was scanned — this channel had zero coverage. The
# upload lane has no handle on the executor's config object (dependency
# direction: executor → upload), so the least-polluting shape is a module
# level registry the executor feeds ONCE at startup with the config's
# environment VALUES (names are not needed — every value is treated as
# secret material; the config block exists to inject env, its values are
# exactly what a crash echo would quote).
_extra_secret_values: frozenset[str] = frozenset()
_secrets_lock = threading.Lock()


def register_secrets(values) -> None:
    """Register additional secret values for the redaction pass (idempotent).

    Fed once at worker startup (executor.py) with the config ``environment``
    block's values; thread-safe because the upload lane may already be
    delivering a restored result while the executor registers (restart
    restore submits tasks before the claim loop starts, but the registry is
    written under a lock either way)."""
    global _extra_secret_values
    with _secrets_lock:
        _extra_secret_values = _extra_secret_values | frozenset(str(value) for value in values)


def _secret_values() -> list[str]:
    """The secret literals to replace: this process's secret-named env values
    plus the registered config-environment values, LONGEST FIRST — a short
    key that is a PREFIX of a longer key must not be replaced first and
    leave an unrecoverable tail fragment behind (#748 R2 P3-4)."""
    values = {
        value
        for name, value in os.environ.items()
        if any(marker in name.upper() for marker in _SECRET_NAME_MARKERS)
    }
    with _secrets_lock:
        values.update(_extra_secret_values)
    return sorted(
        (value for value in values if len(value.encode("utf-8")) > _MIN_SECRET_BYTES),
        key=lambda value: len(value.encode("utf-8")),
        reverse=True,
    )


def redact_secrets(text: str) -> str:
    """Redact secret material from outbound text (best-effort, never raises).

    Three passes: (1) literal replacement of this process's secret env
    values plus the registered worker-config environment values (the exact
    strings the agent env actually carried), longest first so key-prefix
    pairs cannot leave residue; (2) Bearer credentials; (3) secret-shaped
    literals for secrets that did not come from this process (e.g.
    provider keys echoed from a child's own config). Values too short to
    be secrets (<= 8 BYTES) are skipped: replacing short literals mangles
    ordinary text for zero secrecy gain."""
    for value in _secret_values():
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
    rewrite failure TRUNCATES the anchor to empty — better to lose the
    evidence than to ship the secret — and never fails prepare)."""
    tail = scanned_tail
    if not tail:
        sink = run_dir / AGENT_STDERR_FILENAME
        if sink.is_file():
            tail = sink.read_bytes()[:STDERR_TAIL_BYTES]
    if not tail:
        return b""
    redacted = redact_secrets(tail.decode("utf-8", "replace")).encode("utf-8")[:STDERR_TAIL_BYTES]
    if redacted != tail:
        try:
            (run_dir / AGENT_STDERR_FILENAME).write_bytes(redacted)
        except OSError:
            # P3-3(b): a failed rewrite used to leave the RAW sink in place
            # — the archive would then carry the unredacted secret echo.
            # Truncate to empty instead: the evidence is lost, but a secret
            # never leaves the machine (the in-memory redacted tail above
            # still rides the metadata faces). Best-effort, never raises.
            with suppress(OSError):
                (run_dir / AGENT_STDERR_FILENAME).write_bytes(b"")
    return redacted


def stderr_error_message(exit_code: int, stderr_tail: bytes) -> str:
    """error_message for a crashed agent process — exit code plus the
    retained stderr tail's LAST line (the crash header: a panic/trace ends
    the stream, so the newest — and most explanatory — line is the last one;
    the external API's error_summary truncates at 240 chars). The full
    multi-line tail rides the archive member + metadata; the empty tail
    keeps the legacy message unchanged.

    #748 R2 P2-2: redact FIRST, truncate AFTER. Truncating before the
    redaction left a key fragment that straddles the 200-char boundary
    un-replaced (no full-value match), leaking a secret prefix into the
    external error_message face. Redaction replaces values with ``***``
    (shorter), so the 200-char cap keeps its meaning on the redacted text.
    """
    summary = stderr_tail.decode("utf-8", "replace").strip()
    if not summary:
        return f"Agent process exited {exit_code}"
    last_line = " ".join(summary.splitlines()[-1].split())
    return f"Agent process exited {exit_code}: {redact_secrets(last_line)[:200]}"
