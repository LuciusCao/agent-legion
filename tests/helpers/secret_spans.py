"""Literal-match ``SecretSpans`` stub for the stderr redaction tests (#755)."""

from __future__ import annotations

from collections.abc import Callable

from shared.redaction import Span


def literal_spans(*secrets: str) -> Callable[[str], list[Span]]:
    """Spans of every occurrence of each ``secret`` (the shape of the
    Worker's literal pass, without its env registry)."""

    def find(text: str) -> list[Span]:
        spans: list[Span] = []
        for secret in secrets:
            index = text.find(secret)
            while index != -1:
                spans.append((index, index + len(secret)))
                index = text.find(secret, index + 1)
        return spans

    return find
