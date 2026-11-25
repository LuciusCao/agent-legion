"""JobQueries-backed adjunct for the shared-resource service.

All SQL stays in this package and all connections come from the public
JobQueries facade; services never see a DSN or a transaction primitive.
"""

from __future__ import annotations

from typing import Any

from . import creation, lifecycle, reads, source


class SharedResourceQueries:
    def __init__(self, job_db: Any) -> None:
        self._job_db = job_db

    def load_source_snapshot(
        self, workspace_id: str, source_type: str, source_id: str
    ) -> dict[str, Any] | None:
        with self._job_db.read() as conn:
            return source.load_source_snapshot_tx(conn, workspace_id, source_type, source_id)

    def get_resource_creation(self, workspace_id: str, token: str) -> dict[str, Any] | None:
        with self._job_db.read() as conn:
            return creation.resource_creation_tx(conn, workspace_id, token)

    def create_resource_intent(
        self,
        *,
        workspace_id: str,
        name: str,
        token: str,
        request_hash: str,
        manifest_hash: str,
        source_snapshot: dict[str, Any],
        created_by: str,
    ) -> dict[str, Any]:
        with self._job_db.write() as conn:
            conn.execute(
                "select pg_advisory_xact_lock(hashtext('shared-resource-create:' || %s))",
                (f"{workspace_id}:{token}",),
            )
            existing = creation.resource_creation_tx(conn, workspace_id, token)
            if existing is not None:
                return existing
            return creation.insert_resource_intent_tx(
                conn,
                workspace_id=workspace_id,
                name=name,
                token=token,
                request_hash=request_hash,
                manifest_hash=manifest_hash,
                source_snapshot=source_snapshot,
                created_by=created_by,
            )

    def get_version_creation(self, resource_id: str, token: str) -> dict[str, Any] | None:
        with self._job_db.read() as conn:
            return creation.version_creation_tx(conn, resource_id, token)

    def create_version_intent(
        self,
        *,
        resource_id: str,
        token: str,
        request_hash: str,
        manifest_hash: str,
        source_snapshot: dict[str, Any],
        created_by: str,
    ) -> dict[str, Any] | None:
        with self._job_db.write() as conn:
            return creation.insert_version_intent_tx(
                conn,
                resource_id=resource_id,
                token=token,
                request_hash=request_hash,
                manifest_hash=manifest_hash,
                source_snapshot=source_snapshot,
                created_by=created_by,
            )

    def restart_version_attempt(self, version_id: str) -> dict[str, Any] | None:
        with self._job_db.write() as conn:
            return creation.restart_version_attempt_tx(conn, version_id)

    def mark_version_failed(self, version_id: str, generation: int, code: str, detail: str) -> None:
        with self._job_db.write() as conn:
            creation.mark_version_failed_tx(conn, version_id, generation, code, detail)

    def publish_version(self, version_id: str, generation: int) -> dict[str, Any] | None:
        with self._job_db.write() as conn:
            return creation.publish_version_tx(conn, version_id, generation)

    def get_resource(self, workspace_id: str, resource_id: str) -> dict[str, Any] | None:
        with self._job_db.read() as conn:
            return reads.get_resource_tx(conn, workspace_id, resource_id)

    def list_resources(self, workspace_id: str, limit: int, offset: int) -> dict[str, Any]:
        with self._job_db.read() as conn:
            return reads.list_resources_tx(conn, workspace_id, limit, offset)

    def list_versions(self, workspace_id: str, resource_id: str) -> list[dict[str, Any]] | None:
        with self._job_db.read() as conn:
            return reads.list_versions_tx(conn, workspace_id, resource_id)

    def get_visible_version(
        self, workspace_id: str, resource_id: str, version: int
    ) -> dict[str, Any] | None:
        with self._job_db.read() as conn:
            return reads.get_visible_version_tx(conn, workspace_id, resource_id, version)

    def share(
        self,
        *,
        source_workspace_id: str,
        resource_id: str,
        target_workspace_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._job_db.write() as conn:
            return lifecycle.share_resource_tx(
                conn,
                source_workspace_id=source_workspace_id,
                resource_id=resource_id,
                target_workspace_id=target_workspace_id,
                actor_id=actor_id,
            )

    def list_grants(
        self, source_workspace_id: str, resource_id: str
    ) -> list[dict[str, Any]] | None:
        with self._job_db.read() as conn:
            return lifecycle.list_grants_tx(conn, source_workspace_id, resource_id)

    def revoke(
        self,
        *,
        source_workspace_id: str,
        resource_id: str,
        target_workspace_id: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        with self._job_db.write() as conn:
            return lifecycle.revoke_grant_tx(
                conn,
                source_workspace_id=source_workspace_id,
                resource_id=resource_id,
                target_workspace_id=target_workspace_id,
                actor_id=actor_id,
            )

    def select_version(
        self,
        *,
        workspace_id: str,
        resource_id: str,
        version: int,
        expected_revision: int,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._job_db.write() as conn:
            return lifecycle.select_version_tx(
                conn,
                workspace_id=workspace_id,
                resource_id=resource_id,
                version=version,
                expected_revision=expected_revision,
                actor_id=actor_id,
            )

    def resolve_version(
        self,
        *,
        workspace_id: str,
        resource_id: str,
        version: int,
        manifest_hash: str,
        for_new_run: bool,
    ) -> dict[str, Any]:
        with self._job_db.read() as conn:
            return lifecycle.resolve_version_tx(
                conn,
                workspace_id=workspace_id,
                resource_id=resource_id,
                version=version,
                manifest_hash=manifest_hash,
                for_new_run=for_new_run,
            )

    def delete_version(
        self, *, owner_workspace_id: str, resource_id: str, version: int
    ) -> dict[str, Any]:
        with self._job_db.write() as conn:
            return lifecycle.delete_version_tx(
                conn,
                owner_workspace_id=owner_workspace_id,
                resource_id=resource_id,
                version=version,
            )
