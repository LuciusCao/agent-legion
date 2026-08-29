"""Quality replays (schema v29): re-run one sampled node on frozen inputs.

A replay builds an isolated copy job from the original job's frozen workflow
snapshot: the target node's input files are copied into the copy's job
directory, upstream nodes are marked completed, downstream nodes
not_applicable, so only the target node is ever scheduled and the copy
converges to ``completed`` once the target finishes. The original job's
artifacts and state are never touched.

Agent-routed nodes may pin an explicit Agent version (draft/published/
archived — comparing old or candidate versions is the point); the pin is
frozen into the copy batch's source payload and honored at dispatch time
(``resolve_dispatch_agent_definition``). Executor-routed nodes replay as-is.

Replay status is reconciled lazily from the copy job's node row on read —
no hook into the completion path.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.jobs import JobQueries
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    JobServiceError,
    NotFoundError,
)
from server.app.services.quality_artifact_contents import artifact_contents
from server.app.services.quality_replay_setup import QualityReplaySetup
from server.app.services.versioned_entities import VersionedEntityStore
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.definition import WorkflowNode

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("succeeded", "failed")

_REPLAY_COLUMNS = (
    "id, item_id, agent_id, agent_version, replay_job_id, status,"
    " error_message, created_by, created_at, finished_at"
)


class QualityReplayService:
    def __init__(
        self,
        job_db: JobQueries,
        artifact_store: ArtifactStore | None = None,
        object_store: Any = None,
    ) -> None:
        self.job_db = job_db
        self.artifact_store = artifact_store
        self.object_store = object_store

    def create_replay(
        self,
        workspace_id: str,
        item_id: str,
        *,
        agent_version: int | None = None,
        created_by: str = "",
    ) -> dict[str, Any]:
        """Create a replay copy job for one sample item; returns the row."""
        with self.job_db.write() as conn:
            item = self._get_item(conn, workspace_id, item_id)
            node_key = str(item["node_key"])
            job = self._get_original_job(conn, workspace_id, str(item["job_id"]))
            definition = definition_from_job_snapshot(job)
            if definition is None:
                raise InvalidOperationError(
                    "the original job has no frozen workflow snapshot to replay from"
                )
            node = definition.nodes.get(node_key)
            if node is None:
                raise InvalidOperationError(
                    f"node {node_key!r} is not part of the job's frozen workflow snapshot"
                )
            agent_id, pin = self._resolve_agent_pin(
                conn, workspace_id, str(job["workflow_key"]), node, agent_version
            )
            self._reconcile_item_rows(conn, item_id, node_key)
            active = conn.execute(
                "select 1 from quality_replays"
                " where item_id = %s and status in ('pending', 'running') limit 1",
                (item_id,),
            ).fetchone()
            if active is not None:
                raise ConflictError("a replay is already in progress for this sample item")
            replay_id = uuid.uuid4().hex
            conn.execute(
                """
                insert into quality_replays(
                  id, item_id, agent_id, agent_version, created_by
                ) values (%s, %s, %s, %s, %s)
                """,
                (replay_id, item_id, agent_id, pin["version"] if pin else None, created_by),
            )
            row = conn.execute(
                f"select {_REPLAY_COLUMNS} from quality_replays where id = %s", (replay_id,)
            ).fetchone()
        replay = dict(row) if row is not None else {"id": replay_id}
        setup = QualityReplaySetup(self.job_db, self.artifact_store)
        try:
            copy_job_id = setup.build_copy_job(
                workspace_id, item, job, definition, node, replay_id, pin
            )
        except Exception as exc:
            # Business failures (expected, user-relevant) are recorded as a
            # failed replay; programming errors are NOT masked as replay
            # business failures — they leave no row behind and propagate.
            setup.compensate_failed_setup(replay_id, exc)
            if isinstance(exc, JobServiceError):
                raise
            raise InvalidOperationError(f"replay setup failed: {exc}") from exc
        with self.job_db.write() as conn:
            conn.execute(
                "update quality_replays set replay_job_id = %s where id = %s",
                (copy_job_id, replay_id),
            )
        notify_schedulable_work()
        replay["replay_job_id"] = copy_job_id
        return replay

    def list_replays(self, workspace_id: str, item_id: str) -> list[dict[str, Any]]:
        with self.job_db.write() as conn:
            item = self._get_item(conn, workspace_id, item_id)
            self._reconcile_item_rows(conn, item_id, str(item["node_key"]))
            rows = conn.execute(
                f"select {_REPLAY_COLUMNS} from quality_replays"
                " where item_id = %s order by created_at desc, id desc",
                (item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_replay_detail(self, workspace_id: str, replay_id: str) -> dict[str, Any]:
        """Replay row (reconciled) plus its labels and copy-job artifacts."""
        with self.job_db.write() as conn:
            row = conn.execute(
                """
                select r.*, i.node_key as item_node_key
                from quality_replays r
                join quality_sample_items i on i.id = r.item_id
                join quality_sample_batches b on b.id = i.batch_id
                where r.id = %s and b.workspace_id = %s
                """,
                (replay_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Replay not found")
            replay = self._reconcile(conn, dict(row), str(row["item_node_key"]))
            labels = conn.execute(
                """
                select * from quality_labels
                where item_id = %s and target = 'replay' and replay_id = %s
                order by created_at desc, id desc
                """,
                (replay["item_id"], replay_id),
            ).fetchall()
        node_key = str(row["item_node_key"])
        replay.pop("item_node_key", None)
        return {
            "replay": replay,
            "labels": [dict(label) for label in labels],
            "artifacts": artifact_contents(
                self.artifact_store, replay["replay_job_id"], node_key, self.object_store
            ),
            "input_artifacts": self._input_artifacts(replay["replay_job_id"], node_key),
        }

    # -- creation helpers -------------------------------------------------

    def _get_item(
        self, conn: DatabaseConnection, workspace_id: str, item_id: str
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            select i.id, i.batch_id, i.job_id, i.node_key
            from quality_sample_items i
            join quality_sample_batches b on b.id = i.batch_id
            where i.id = %s and b.workspace_id = %s
            """,
            (item_id, workspace_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("Sample item not found")
        return dict(row)

    @staticmethod
    def _get_original_job(
        conn: DatabaseConnection, workspace_id: str, job_id: str
    ) -> dict[str, Any]:
        row = conn.execute(
            "select * from jobs where id = %s and workspace_id = %s",
            (job_id, workspace_id),
        ).fetchone()
        if row is None:
            raise InvalidOperationError(
                "the original job no longer exists; its frozen inputs cannot be replayed"
            )
        return dict(row)

    def _resolve_agent_pin(
        self,
        conn: DatabaseConnection,
        workspace_id: str,
        workflow_key: str,
        node: WorkflowNode,
        agent_version: int | None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Resolve the Agent version pin; executor nodes replay unpinned."""
        route = conn.execute(
            """
            select target_kind, target_id from workspace_node_routes
            where workspace_id = %s and workflow_key = %s and node_key = %s
            """,
            (workspace_id, workflow_key, node.key),
        ).fetchone()
        if route is None:
            raise InvalidOperationError(f"node {node.key!r} has no workspace route; cannot replay")
        if str(route["target_kind"]) != "agent":
            if agent_version is not None:
                raise InvalidOperationError("agent_version pins apply to Agent-routed nodes only")
            return "", None
        agent_id = str(route["target_id"])
        store = VersionedEntityStore(self.job_db, "agent")
        entity = (
            store.get_published(agent_id, workspace_id)
            if agent_version is None
            else store.get_version(agent_id, agent_version, workspace_id)
        )
        if entity is None:
            if agent_version is None:
                raise InvalidOperationError(
                    f"Agent {agent_id!r} has no published version in workspace"
                    f" {workspace_id!r} to replay with; agent definitions are"
                    " workspace-scoped — create one in Studio (Agent 管理) first"
                )
            raise NotFoundError(
                f"Agent {agent_id!r} has no version {agent_version} in workspace {workspace_id!r}"
            )
        capability = str(entity.definition.get("capability") or "")
        if capability != node.capability:
            raise InvalidOperationError(
                f"Agent {agent_id!r} v{entity.version} capability {capability!r}"
                f" does not match node capability {node.capability!r}"
            )
        pin = {
            "agent_id": agent_id,
            "version": entity.version,
            "definition_hash": entity.definition_hash,
        }
        return agent_id, pin

    # -- status reconciliation ---------------------------------------------

    def _reconcile_item_rows(self, conn: DatabaseConnection, item_id: str, node_key: str) -> None:
        rows = conn.execute(
            f"select {_REPLAY_COLUMNS} from quality_replays"
            " where item_id = %s and status not in ('succeeded', 'failed')",
            (item_id,),
        ).fetchall()
        for row in rows:
            self._reconcile(conn, dict(row), node_key)

    def _reconcile(
        self, conn: DatabaseConnection, replay: dict[str, Any], node_key: str
    ) -> dict[str, Any]:
        """Derive the live status from the copy job's target node row."""
        if replay["status"] in _TERMINAL_STATUSES or not replay["replay_job_id"]:
            return replay
        node = conn.execute(
            "select status, error_message from job_nodes where job_id = %s and node_key = %s",
            (replay["replay_job_id"], node_key),
        ).fetchone()
        if node is None:
            derived, error = "failed", "replay copy job was cleaned up before the replay finished"
        else:
            node_status = str(node["status"])
            if node_status == "completed":
                derived, error = "succeeded", ""
            elif node_status == "failed":
                derived, error = "failed", str(node["error_message"] or "")
            elif node_status == "running":
                derived, error = "running", ""
            elif node_status in ("pending", "ready", "stale"):
                derived, error = "pending", ""
            else:
                derived, error = "failed", f"target node became {node_status} in the copy job"
        if derived == replay["status"]:
            return replay
        if derived in _TERMINAL_STATUSES:
            conn.execute(
                "update quality_replays set status = %s, error_message = %s,"
                " finished_at = current_timestamp where id = %s",
                (derived, error, replay["id"]),
            )
        else:
            conn.execute(
                "update quality_replays set status = %s where id = %s",
                (derived, replay["id"]),
            )
        refreshed = conn.execute(
            f"select {_REPLAY_COLUMNS} from quality_replays where id = %s", (replay["id"],)
        ).fetchone()
        return dict(refreshed) if refreshed is not None else replay

    def _input_artifacts(self, replay_job_id: str, node_key: str) -> list[dict[str, Any]]:
        """Frozen upstream inputs shared with the copy job (comparison aid)."""
        if not replay_job_id:
            return []
        upstream: set[str] = set()
        if self.artifact_store is not None:
            upstream = {
                str(ref["node_key"]) for ref in self.artifact_store.refs_for_job(replay_job_id)
            }
        if self.object_store is not None and self.object_store.enabled:
            upstream |= {
                str(row["node_key"]) for row in self.object_store.rows_for_job(replay_job_id)
            }
        upstream -= {node_key}
        if not upstream:
            return []
        return artifact_contents(self.artifact_store, replay_job_id, upstream, self.object_store)
