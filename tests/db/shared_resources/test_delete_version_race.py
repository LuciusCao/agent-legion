from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from server.app.jobs.queries.shared_resources.lifecycle import (
    delete_version_tx,
    insert_job_pin_tx,
    resolve_version_tx,
)

_RESOURCE = "resource-delete-race"
_V1 = "version-delete-race-v1"
_V2 = "version-delete-race-v2"
_OWNER = "delete-race-owner"
_TARGET = "delete-race-target"
_TARGET_GRANT = "grant-delete-race-target"
_V1_HASH = "1" * 64
_TIMEOUT_SECONDS = 30.0


def _seed_owner_v1_target_v2(job_db) -> None:
    with job_db.write() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Owner'), (%s, 'Target')",
            (_OWNER, _TARGET),
        )
        conn.execute(
            "insert into shared_resources(id, owner_workspace_id, name, create_token,"
            " create_request_hash, created_by) values"
            " (%s, %s, 'Race resource', 'create-race', 'request-race', 'actor')",
            (_RESOURCE, _OWNER),
        )
        for version_id, version, manifest_hash in ((_V1, 1, _V1_HASH), (_V2, 2, "2" * 64)):
            conn.execute(
                "insert into shared_resource_versions(id, resource_id, version, status,"
                " source_type, source_id, source_workspace_id, source_snapshot_json,"
                " manifest_hash, idempotency_key, request_hash, created_by, ready_at)"
                " values (%s, %s, %s, 'ready', 'material', 'source', %s, '{\"files\": []}',"
                " %s, %s, 'request', 'actor', current_timestamp)",
                (version_id, _RESOURCE, version, _OWNER, manifest_hash, f"token-{version}"),
            )
        for grant_id, workspace_id in (
            ("grant-delete-race-owner", _OWNER),
            (_TARGET_GRANT, _TARGET),
        ):
            conn.execute(
                "insert into shared_resource_grants(id, resource_id, workspace_id, status, created_by)"
                " values (%s, %s, %s, 'active', 'actor')",
                (grant_id, _RESOURCE, workspace_id),
            )
        for grant_id, workspace_id, version_id in (
            ("grant-delete-race-owner", _OWNER, _V1),
            (_TARGET_GRANT, _TARGET, _V2),
        ):
            conn.execute(
                "insert into shared_resource_selections(grant_id, workspace_id, resource_id,"
                " version_id, revision, selected_by) values (%s, %s, %s, %s, 1, 'actor')",
                (grant_id, workspace_id, _RESOURCE, version_id),
            )


class _DeleteVersionConnection:
    def __init__(self, conn, version_lock_attempted: Event, allow_version_lock: Event) -> None:
        self._conn = conn
        self.backend_pid = conn._raw.info.backend_pid
        self._version_lock_attempted = version_lock_attempted
        self._allow_version_lock = allow_version_lock

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        if normalized.startswith("select * from shared_resource_versions") and normalized.endswith(
            "for update"
        ):
            self._version_lock_attempted.set()
            assert self._allow_version_lock.wait(timeout=_TIMEOUT_SECONDS)
        return self._conn.execute(query, params)


def test_delete_version_serializes_with_workspace_delete(job_db) -> None:
    _seed_owner_v1_target_v2(job_db)
    version_lock_attempted = Event()
    allow_version_lock = Event()
    workspace_delete_started = Event()
    backend_pids: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=2) as pool:

        def delete_v1() -> dict:
            with job_db.write() as conn:
                guarded = _DeleteVersionConnection(conn, version_lock_attempted, allow_version_lock)
                backend_pids["version_delete"] = guarded.backend_pid
                return delete_version_tx(
                    guarded,
                    owner_workspace_id=_OWNER,
                    resource_id=_RESOURCE,
                    version=1,
                )

        version_deletion = pool.submit(delete_v1)
        assert version_lock_attempted.wait(timeout=_TIMEOUT_SECONDS)
        with job_db.read() as conn:
            resource_locked = (
                conn.execute(
                    "select 1 from pg_locks where pid=%s"
                    " and relation='shared_resources'::regclass"
                    " and mode='RowShareLock' and granted limit 1",
                    (backend_pids["version_delete"],),
                ).fetchone()
                is not None
            )

        def delete_owner() -> None:
            workspace_delete_started.set()
            job_db.delete_workspace(_OWNER)

        workspace_deletion = pool.submit(delete_owner)
        assert workspace_delete_started.wait(timeout=_TIMEOUT_SECONDS)
        if not resource_locked:
            workspace_deletion.result(timeout=_TIMEOUT_SECONDS)
        allow_version_lock.set()
        version_result = version_deletion.result(timeout=_TIMEOUT_SECONDS)
        workspace_deletion.result(timeout=_TIMEOUT_SECONDS)

    assert version_result["status"] in {"in_use", "not_found"}
    assert version_result["status"] != "deleted"
    with job_db.read() as conn:
        assert (
            conn.execute(
                "select deleted_at from shared_resource_versions where id=%s", (_V1,)
            ).fetchone()["deleted_at"]
            is None
        )
        assert (
            conn.execute(
                "select status from shared_resource_grants where id=%s", (_TARGET_GRANT,)
            ).fetchone()["status"]
            == "active"
        )
        assert (
            conn.execute(
                "select version_id from shared_resource_selections where grant_id=%s",
                (_TARGET_GRANT,),
            ).fetchone()["version_id"]
            == _V2
        )
        assert (
            conn.execute(
                "select owner_deleted_at from shared_resources where id=%s", (_RESOURCE,)
            ).fetchone()["owner_deleted_at"]
            is not None
        )

    for new_run in (False, True):
        with job_db.read() as conn:
            resolved = resolve_version_tx(
                conn,
                workspace_id=_TARGET,
                resource_id=_RESOURCE,
                version=1,
                manifest_hash=_V1_HASH,
                for_new_run=new_run,
            )
        assert resolved["status"] == "ready"
    with job_db.write() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, input_json)"
            " values ('delete-race-target-job', %s, 'test', 'pin', 'queued', '{}')",
            (_TARGET,),
        )
        insert_job_pin_tx(
            conn,
            job_id="delete-race-target-job",
            resource_id=_RESOURCE,
            version_id=_V1,
            manifest_hash=_V1_HASH,
            grant_id=_TARGET_GRANT,
        )
