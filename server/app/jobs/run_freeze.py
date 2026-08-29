"""Job-domain run-freeze helpers shared by queries and services (issue #195).

``candidate_input`` rebuilds the job input document from a task-candidate
row; it lives here (jobs domain, importable from ``jobs/queries`` without
reversing into ``services``) while the dispatch-facing payload rebuilders
stay in ``services.run_payload``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def candidate_input(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """The job input document for a task candidate.

    Runs created from items (RunService) carry the terminal input document on
    the candidate verbatim. Every legacy (pre-materials) intake candidate gets
    the ``ref`` shape with the legacy marker; ``external_id`` mirrors the
    job's ``source_id`` and ``connection_key`` stays empty until a workflow
    binds a source connection (design §7).
    """
    explicit = candidate.get("input")
    if isinstance(explicit, Mapping):
        return dict(explicit)
    input_doc: dict[str, Any] = {
        "type": "ref",
        "connection_key": "",
        "external_id": str(candidate["entity_id"]),
        "legacy": True,
    }
    for key in ("entity_type", "title", "stem"):
        value = candidate.get(key)
        if value not in (None, ""):
            input_doc[key] = str(value)
    return input_doc
