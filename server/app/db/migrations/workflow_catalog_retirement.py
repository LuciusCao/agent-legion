"""Workflow catalog retirement (schema v50, issue #112).

The global workflow key registry (``workflow_catalog``, schema v40) was the
last global concept on the execution path: agent definitions went strictly
workspace-scoped at schema v46 and workflow revisions have always been
per-workspace, so a workflow is now simply the DAG inside one workspace
(``workspaces.default_workflow_key`` degrades to a plain text identifier;
old job rows keep their ``workflow_key`` as text).

Migration steps:

1. Fold registered keys into their workspaces. Registered catalog rows carry
   no executable content (``definition_json`` is NULL for origin='registered';
   the first workspace draft publish created revision v1 per workspace), so
   there is nothing to copy — the workspace's own ``workflow_revisions`` rows
   are already the authoritative DAG. Rows whose key no workspace references
   are logged and dropped with the table.
2. Drop the ``workflow_catalog`` table.

The built-in demo workflow definition stays in the repo
(``server/app/workflows/builtin.py``) as the optional sample-template seed
for new workspaces. Idempotent on replay: the drop is if-exists and the
fold only reads the table when present.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def migrate_workflow_catalog_retirement(conn: Any) -> None:
    """Fold catalog knowledge into workspaces, then drop the table (v50)."""
    if not conn.execute("select to_regclass('workflow_catalog')").fetchone()["to_regclass"]:
        return
    referenced = {
        str(row["default_workflow_key"])
        for row in conn.execute("select distinct default_workflow_key from workspaces").fetchall()
        if row["default_workflow_key"]
    }
    for row in conn.execute("select key, origin from workflow_catalog order by key").fetchall():
        key = str(row["key"])
        if row["origin"] == "registered" and key not in referenced:
            logger.warning(
                "workflow catalog retirement: registered workflow %r is not referenced by"
                " any workspace; its registry row is dropped with the table (per-workspace"
                " revisions, if any, are unaffected)",
                key,
            )
    conn.execute("drop table if exists workflow_catalog")
