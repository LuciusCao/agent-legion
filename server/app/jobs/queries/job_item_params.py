"""Write-side frozen-params verification for tokenized run items (#1565).

The helpers run inside ``create_jobs_bulk``'s existing job-mutation lock
domain. They deliberately live in the jobs domain: no service import is
needed to recognize the #813 token suffix on server-generated material/bundle
source ids, and ordinary token-less dedup keeps its first-writer-wins shape.
"""

from __future__ import annotations

import json
from typing import Any

from server.app.db.rowmap import parse_object

_INPUT_BY_IDS_SQL = "select id, input_json from jobs where id in ({ids})"
_TOKEN_SOURCE_TYPES = ("material", "bundle")


def _canonical_params(input_json: Any) -> str:
    document = parse_object(input_json)
    return json.dumps(
        document.get("params", {}),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def verify_chunk_item_params(conn: Any, chunk: list[tuple[Any, ...]]) -> None:
    """Reject same-token params drift under the chunk's job-mutation locks.

    ``chunk`` uses ``job_row_tuple``'s column order. The actual row ids already
    include any shared-resource pins hash, so this check covers both ordinary
    and shared-resource executions. Existing rows are read only for tokenized
    material/bundle sources, avoiding input payload reads for every other job.
    """
    token_rows = [row for row in chunk if row[2] in _TOKEN_SOURCE_TYPES and "~" in str(row[3])]
    if not token_rows:
        return
    job_ids = [str(row[0]) for row in token_rows]
    placeholders = ",".join("%s" for _ in job_ids)
    rows = conn.execute(_INPUT_BY_IDS_SQL.format(ids=placeholders), job_ids).fetchall()
    by_id = {str(row["id"]): row for row in rows}
    for row in token_rows:
        current = by_id.get(str(row[0]))
        if current is None or _canonical_params(current["input_json"]) == _canonical_params(
            row[12]
        ):
            continue
        entity_id, _, token = str(row[3]).partition("~")
        raise ValueError(
            f"client_token {token!r} was already used with different params for "
            f"{row[2]} {entity_id!r}"
        )
