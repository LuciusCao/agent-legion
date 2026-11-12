"""Job-id injective encoding (#1255) — pure-function contract tests.

The legacy ``{ws}_{workflow_key}_{source_id}`` concatenation was not
injective: workspace ``a`` + source ``a_a_z`` and workspace ``a_a`` +
source ``z`` both produced ``a_a_a_a_z``, so a bulk creation could lock a
job row owned by a foreign workspace (PR #1243 R5). These tests pin the
escape encoding (segment escapes ``~`` → ``~~``, ``_`` → ``~u``,
``/`` → ``~s``; segments joined by a bare ``_``): the issue's collision
example now yields distinct ids, the encode is reversible, and segments
without special characters keep their legacy spelling (most existing ids
are unchanged).
"""

from __future__ import annotations

import pytest

from server.app.jobs.job_id_encoding import decode_job_id, encode_job_id

pytestmark = pytest.mark.no_db

# The issue's collision pair, the legacy normalize pair (``/`` was mapped
# to ``_``), and separator-boundary shapes that defeat naive ``_``
# doubling (``("ws", "wf_", "_")`` vs ``("ws", "wf", "__")`` both encode
# to ``ws_wf____`` under ``_`` → ``__`` — see the module docstring).
COLLIDING_TRIPLES: list[tuple[tuple[str, str, str], tuple[str, str, str]]] = [
    # #1255's example: workspace a + source a_a_z vs workspace a_a + source z.
    (("a", "wf", "a_a_z"), ("a_a", "wf", "z")),
    # Legacy normalize collision: source a/b vs source a_b in one workspace.
    (("ws", "wf", "a/b"), ("ws", "wf", "a_b")),
    # Boundary ambiguity of the naive doubling escape.
    (("ws", "wf_", "_"), ("ws", "wf", "__")),
    # Trailing vs leading separator-adjacent underscores.
    (("ws", "wf", "x_"), ("ws", "wf_", "x")),
    # Whitespace was stripped away before encoding (lossy): now distinct.
    (("ws", "wf", "x"), ("ws", "wf", " x")),
    (("ws", "wf", "x"), ("ws", "wf", "x ")),
]

ROUND_TRIP_TRIPLES: list[tuple[str, str, str]] = [
    ("a", "wf", "z"),
    ("a", "wf", "a_a_z"),
    ("a_a", "wf", "z"),
    ("ws", "wf", "a/b"),
    ("ws", "wf", "__"),
    ("ws", "wf", "_/_"),
    ("ws", "wf", "~~"),
    ("ws", "wf", "~u"),
    ("ws", "wf", "~s"),
    ("ws", "wf", "~"),
    ("ws-1", "wf", ""),
    ("ws", "wf", "中文 题目/第_1 题"),
    ("ws", "wf", "x" * 300),
]


@pytest.mark.parametrize(("left", "right"), COLLIDING_TRIPLES)
def test_legacy_colliding_triples_now_produce_distinct_ids(
    left: tuple[str, str, str], right: tuple[str, str, str]
) -> None:
    assert encode_job_id(*left) != encode_job_id(*right)


@pytest.mark.parametrize(("ws", "wf", "source_id"), ROUND_TRIP_TRIPLES)
def test_encode_decode_round_trip(ws: str, wf: str, source_id: str) -> None:
    assert decode_job_id(encode_job_id(ws, wf, source_id)) == (ws, wf, source_id)


def test_segments_without_specials_keep_the_legacy_spelling() -> None:
    # Most existing ids are unchanged: only segments containing "_", "/",
    # "~" (or surrounding whitespace, no longer stripped) move to a new
    # spelling.
    assert encode_job_id("ws-sb", "wf", "item-00000") == "ws-sb_wf_item-00000"


def test_escaped_id_alphabet_stays_path_and_url_safe() -> None:
    job_id = encode_job_id("a_a", "w_f", "x/y_z~")
    assert job_id == "a~ua_w~uf_x~sy~uz~~"
    # Escaping never introduces "/": ids stay single safe path components
    # (storage dirs, S3 keys, URL path segments), and every id still
    # contains the "_" separators.
    assert "/" not in job_id
    assert "_" in job_id


def test_decode_rejects_wrong_segment_count() -> None:
    with pytest.raises(ValueError, match="Malformed encoded job id"):
        decode_job_id("ws_wf_src_extra")
    with pytest.raises(ValueError, match="Malformed encoded job id"):
        decode_job_id("wf-only")


def test_decode_rejects_bad_digraphs() -> None:
    with pytest.raises(ValueError, match="Malformed encoded job id segment"):
        decode_job_id("ws_wf_bad~x")
    with pytest.raises(ValueError, match="Malformed encoded job id segment"):
        decode_job_id("ws_wf_trailing~")


def test_decode_accepts_empty_source_segment() -> None:
    assert decode_job_id("ws_wf_") == ("ws", "wf", "")
