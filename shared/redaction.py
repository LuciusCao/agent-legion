"""Span-based secret redaction primitive (#755).

A redactor reports WHERE secrets are (``SecretSpans``: text → ``(start, end)``
character spans) instead of returning rewritten text. Callers that cut text
need the positions: ``shared/pi_events.py`` must keep a lookback region out
of its output and widen its cut to the start of any secret straddling it,
neither of which is possible behind a text-in/text-out black box. The
Worker-side registry (worker/upload/stderr_evidence.py) produces the spans;
this module only merges and applies them. Stdlib-only (see
``shared/__init__.py``).

#844: ``SecretRedactor`` is the registry's read face — ONE immutable
snapshot carrying both the span function and the longest-literal length.
The old call-site shape (read spans, read max length) could observe the
registry at two different times; a value registered in between widened the
span function but not the lookback margin, so a cut could split a secret
the spans would have matched whole. ``max_chars`` is by construction ≥ the
longest literal the snapshot's span function can match — the lookback
contract text-cutting callers need.

#842: ``redact_json`` rewrites ONLY string values (keys, numbers, booleans
and null are never touched), so the result is always valid JSON with an
identical structure — the compressed events.jsonl stays parseable by the
Host renderer. Matching runs on the DECODED string (json.loads has already
turned ``\n`` / ``\"`` / ``\u0061`` escapes into real characters), which
covers the escaped wire form of every registered literal.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

REDACTED = "***"

Span = tuple[int, int]
SecretSpans = Callable[[str], Iterable[Span]]


@dataclass(frozen=True)
class SecretRedactor:
    """Immutable one-read snapshot of a secret registry (#844).

    ``spans`` locates secret material in decoded text (may raise through —
    the fail-closed policy belongs to the caller that owns the output
    face); ``max_chars`` is the longest literal those spans can match in
    characters, the lookback length text-cutting callers must keep. Both
    fields describe the SAME registry read: a later registration produces a
    new snapshot, never a mutation of one already in hand.
    """

    spans: SecretSpans
    max_chars: int

    def redact(self, text: str) -> str:
        """``text`` with every span region replaced by ``REDACTED``."""
        if not text:
            return text
        return apply_spans(text, self.spans(text))

    def redact_json(self, value: Any) -> Any:
        """``value`` with every nested JSON string VALUE redacted.

        Keys are left alone (a secret-named key like ``TOKEN`` is not
        secret material; a secret value used as a key is a documented
        boundary), and non-string leaves pass through untouched, so the
        output re-serializes to the same shape.
        """
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_json(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact_json(item) for key, item in value.items()}
        return value


def merge_spans(spans: Iterable[Span]) -> list[Span]:
    """Sorted, non-overlapping cover of ``spans`` (overlapping or touching
    spans are fused — a short secret nested in or abutting a longer one is
    redacted as one region, never leaving a residue between them)."""
    merged: list[Span] = []
    for start, end in sorted(span for span in spans if span[1] > span[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def apply_spans(text: str, spans: Iterable[Span], offset: int = 0) -> str:
    """Replace each merged span with ``REDACTED``. ``offset`` is where
    ``text`` starts in the coordinates the spans were computed in; spans
    are clipped to the text."""
    pieces: list[str] = []
    cursor = 0
    for start, end in merge_spans(spans):
        start, end = max(start - offset, 0), min(end - offset, len(text))
        if end <= cursor or start >= end:
            continue
        pieces.append(text[cursor : max(start, cursor)])
        pieces.append(REDACTED)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
