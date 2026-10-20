"""Injective job-id encoding (#1255).

Job ids are deterministic: ``encode_job_id(workspace_id, workflow_key,
source_id)``. The legacy shape concatenated raw segments with ``_`` (and
mapped ``/`` to ``_`` inside the source id), which is not injective —
workspace ``a`` + source ``a_a_z`` and workspace ``a_a`` + source ``z``
both produced ``a_a_a_a_z``, so a bulk creation could lock a job row owned
by a FOREIGN workspace (PR #1243 R5).

The encoding escapes inside each segment with ``~`` as the escape
character (``~`` → ``~~``, ``_`` → ``~u``, ``/`` → ``~s``) and joins
segments with a bare ``_``. Escaped segments contain no ``_`` at all, so
every ``_`` in the result is a separator and the mapping from
``(workspace_id, workflow_key, source_id)`` to job id is a bijection
(``decode_job_id`` is its inverse). Note the simpler ``_`` → ``__``
doubling is NOT injective at segment boundaries (``("ws", "wf_", "_")``
and ``("ws", "wf", "__")`` both encode to ``ws_wf____``) — that is why
the escape character is ``~`` rather than ``_`` itself.

Segments without ``_``/``/``/``~`` keep their legacy spelling, so most
existing id shapes are unchanged. Escaping never introduces ``/`` or
control characters, so ids stay single safe path components (storage
dirs, S3 keys) and URL path segments, and every id still contains the
``_`` separators.

Backward compatibility: existing rows keep their legacy ids. Ids are
opaque primary keys everywhere — no consumer parses them back into
segments (``decode_job_id`` exists for tests and debugging, not runtime
lookups) — and resubmission dedup matches on the ``(source_type,
source_id)`` columns, never on a recomputed id, so legacy jobs keep their
dedup behavior. Only newly created jobs get the new encoding. A legacy id
that coincidentally equals a new encoded id under a different identity is
caught by the existing identity checks (``create_job``'s existing-row
check, ``verify_chunk_identities``) as a structured collision error.
"""

from __future__ import annotations

_DECODE = {"~": "~", "u": "_", "s": "/"}


def _encode_segment(segment: str) -> str:
    # "~" must be escaped first: it introduces every other digraph.
    out = segment.replace("~", "~~")
    return out.replace("_", "~u").replace("/", "~s")


def encode_job_id(workspace_id: str, workflow_key: str, source_id: str) -> str:
    """Deterministic job id, injective in (workspace_id, workflow_key, source_id).

    The source id is encoded verbatim — no strip/normalize: any lossy
    pre-pass would break injectivity.
    """
    return "_".join(
        (
            _encode_segment(workspace_id),
            _encode_segment(workflow_key),
            _encode_segment(source_id),
        )
    )


def _decode_segment(encoded: str) -> str:
    """Inverse of ``_encode_segment``; raise ValueError on a bad digraph."""
    out: list[str] = []
    i = 0
    while i < len(encoded):
        ch = encoded[i]
        if ch == "~":
            nxt = encoded[i + 1] if i + 1 < len(encoded) else ""
            decoded = _DECODE.get(nxt)
            if decoded is None:
                raise ValueError(f"Malformed encoded job id segment: {encoded!r}")
            out.append(decoded)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def decode_job_id(job_id: str) -> tuple[str, str, str]:
    """Inverse of ``encode_job_id``; raise ValueError on malformed input.

    Only ids produced by ``encode_job_id`` are decodable — legacy pre-#1255
    ids are raw concatenations and must never be fed here.
    """
    parts = job_id.split("_")
    if len(parts) != 3:
        raise ValueError(f"Malformed encoded job id (segment count): {job_id!r}")
    return (
        _decode_segment(parts[0]),
        _decode_segment(parts[1]),
        _decode_segment(parts[2]),
    )
