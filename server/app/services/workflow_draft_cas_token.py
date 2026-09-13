"""CAS token (``expected_updated_at``) parsing for the workflow draft store.

Split from ``workflow_draft_cas.py`` (#633 codex review P2-2, budget): the
tool-surface and human draft-store contracts pre-validate with the shared
helper so an unparseable token is a 422 instead of a DB error (500) from
the ``updated_at = %s::timestamptz`` cast.
"""

from __future__ import annotations

from datetime import datetime

from server.app.jobs.queries.workflow_drafts import DRAFT_NEVER_SAVED

CAS_TIMESTAMP_HINT = "expected_updated_at must be an ISO timestamp or the literal 'never-saved'"


def parse_cas_timestamp(value: str) -> bool:
    """Is ``value`` the never-saved marker or a parseable ISO timestamp?

    Renderings seen on the wire include Postgres ``+00``-style offsets and a
    trailing ``Z``; ``fromisoformat`` (Python 3.11+) accepts both natively,
    with the ``Z`` suffix normalized for older quirk renderings.
    """
    if value == DRAFT_NEVER_SAVED:
        return True
    try:
        datetime.fromisoformat(value.removesuffix("Z"))
    except ValueError:
        return False
    return True
