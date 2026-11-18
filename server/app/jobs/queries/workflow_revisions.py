"""Workflow revision writes on the JobQueries facade.

The transaction around archive-then-insert lives here; historical projection
pruning is in ``workflow_revision_projection.py`` and revision snapshot reads
in ``workflow_revision_reads.py``. ``WorkflowRevisionQueriesMixin`` keeps those
reads and the revision write transaction behind the JobQueries facade.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.jobs.queries.workflow_revision_projection import (
    create_workflow_revision_with_projection,
)
from server.app.jobs.queries.workflow_revision_reads import (
    WorkflowRevisionReadQueriesMixin,
)


class WorkflowRevisionQueriesMixin(WorkflowRevisionReadQueriesMixin):
    def create_workflow_revision(
        self,
        *,
        revision_id: str,
        workspace_id: str,
        workflow_key: str,
        version: int,
        status: str,
        definition_json: str,
        definition_hash: str,
        on_commit: Callable[[DatabaseConnection], None] | None = None,
        frozen_route_nodes: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Insert one revision row; ``on_commit`` rides the same transaction.

        The publish pipeline applies the workspace override prune through the
        hook — atomically with the insert, so a prune failure rolls the whole
        publish back (codex 终轮 P1-3). Planning is the caller's pre-read.
        """
        # One transaction: archive the previous active revision, insert the
        # new one, prune stale historical projections, and run the caller's
        # in-transaction hook (#287; see workflow_revision_projection for the
        # helper contracts).
        with self.connect() as conn:
            row = create_workflow_revision_with_projection(
                conn,
                revision_id=revision_id,
                workspace_id=workspace_id,
                workflow_key=workflow_key,
                version=version,
                status=status,
                definition_json=definition_json,
                definition_hash=definition_hash,
                frozen_route_nodes=frozen_route_nodes,
            )
            if on_commit is not None:
                on_commit(conn)
        if row is None:
            raise RuntimeError("workflow revision insert did not return a row")
        return dict(row)


def compose_commit_hooks(
    *hooks: Callable[[DatabaseConnection], None] | None,
) -> Callable[[DatabaseConnection], None] | None:
    """Merge optional revision-transaction hooks into one (None when all None).

    The publish pipeline stacks caller hooks on the built-in override prune
    (e.g. #1221's draft-row delete on draft publish): every hook runs inside
    the revision transaction in argument order, so any failure rolls the
    whole publish back.
    """
    active = [hook for hook in hooks if hook is not None]
    if not active:
        return None

    def _run(conn: DatabaseConnection) -> None:
        for hook in active:
            hook(conn)

    return _run
