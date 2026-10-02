"""Span-based secret redaction primitive (#755).

A redactor reports WHERE secrets are (``SecretSpans``: text → ``(start, end)``
character spans) instead of returning rewritten text. Callers that cut text
need the positions: ``shared/pi_events.py`` must keep a lookback region out
of its output and widen its cut to the start of any secret straddling it,
neither of which is possible behind a text-in/text-out black box. The
Worker-side registry (worker/upload/stderr_evidence.py) produces the spans;
this module only merges and applies them. Stdlib-only (see
``shared/__init__.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

REDACTED = "***"

Span = tuple[int, int]
SecretSpans = Callable[[str], Iterable[Span]]


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
