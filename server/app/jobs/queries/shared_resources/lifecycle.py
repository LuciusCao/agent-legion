"""Grant, selection, exact-pin and logical-deletion transactions."""

from __future__ import annotations

import uuid
from typing import Any

from .creation import version_with_files_tx


def insert_job_pin_tx(
    conn: Any,
    *,
    job_id: str,
    resource_id: str,
    version_id: str,
    manifest_hash: str,
    grant_id: str,
) -> None:
    """Insert one accept-time pin while serializing with logical version deletion.

    M43 calls this inside the same transaction as run/job creation. A repeated
    job/resource pin is accepted only when every frozen identity field matches.
    """
    version = conn.execute(
        "select id, manifest_hash from shared_resource_versions"
        " where id=%s and resource_id=%s and status='ready' and deleted_at is null"
        " for key share",
        (version_id, resource_id),
    ).fetchone()
    if version is None or str(version["manifest_hash"]) != manifest_hash:
        raise ValueError("shared resource pin does not match its ready version")
    grant = conn.execute(
        "select id, workspace_id from shared_resource_grants"
        " where id=%s and resource_id=%s and status='active' for key share",
        (grant_id, resource_id),
    ).fetchone()
    job = conn.execute("select workspace_id from jobs where id=%s", (job_id,)).fetchone()
    if grant is None or job is None or str(job["workspace_id"]) != str(grant["workspace_id"]):
        raise ValueError("shared resource pin requires an active grant in the job workspace")
    conn.execute(
        "insert into shared_resource_job_pins(job_id, resource_id, version_id, manifest_hash, grant_id)"
        " values (%s, %s, %s, %s, %s) on conflict (job_id, resource_id) do nothing",
        (job_id, resource_id, version_id, manifest_hash, grant_id),
    )
    existing = conn.execute(
        "select * from shared_resource_job_pins where job_id=%s and resource_id=%s",
        (job_id, resource_id),
    ).fetchone()
    if existing is None or any(
        str(existing[key]) != value
        for key, value in {
            "version_id": version_id,
            "manifest_hash": manifest_hash,
            "grant_id": grant_id,
        }.items()
    ):
        raise ValueError("a job cannot change its frozen shared resource pin")


def share_resource_tx(
    conn: Any,
    *,
    source_workspace_id: str,
    resource_id: str,
    target_workspace_id: str,
    actor_id: str,
) -> dict[str, Any]:
    resource = conn.execute(
        "select * from shared_resources where id=%s and owner_workspace_id=%s"
        " and owner_deleted_at is null for update",
        (resource_id, source_workspace_id),
    ).fetchone()
    if resource is None:
        return {"status": "not_found"}
    target = conn.execute(
        "select id from workspaces where id=%s", (target_workspace_id,)
    ).fetchone()
    initial = conn.execute(
        "select id, version from shared_resource_versions"
        " where resource_id=%s and status='ready' and deleted_at is null"
        " order by version desc limit 1 for key share",
        (resource_id,),
    ).fetchone()
    if target is None or initial is None:
        return {"status": "not_found"}
    grant = conn.execute(
        "select * from shared_resource_grants where resource_id=%s and workspace_id=%s for update",
        (resource_id, target_workspace_id),
    ).fetchone()
    duplicate = grant is not None and str(grant["status"]) == "active"
    if grant is None:
        grant_id = uuid.uuid4().hex
        conn.execute(
            "insert into shared_resource_grants(id, resource_id, workspace_id, status, created_by)"
            " values (%s, %s, %s, 'active', %s)",
            (grant_id, resource_id, target_workspace_id, actor_id),
        )
    else:
        grant_id = str(grant["id"])
        if not duplicate:
            conn.execute(
                "update shared_resource_grants set status='active', revoked_by='', revoked_at=null"
                " where id=%s",
                (grant_id,),
            )
    selection = conn.execute(
        "select * from shared_resource_selections where grant_id=%s", (grant_id,)
    ).fetchone()
    if selection is None:
        conn.execute(
            "insert into shared_resource_selections("
            " grant_id, workspace_id, resource_id, version_id, revision, selected_by"
            ") values (%s, %s, %s, %s, 1, %s)",
            (grant_id, target_workspace_id, resource_id, initial["id"], actor_id),
        )
    current = conn.execute(
        "select g.*, s.version_id, s.revision, s.selected_by, s.updated_at as selected_at"
        " from shared_resource_grants g join shared_resource_selections s on s.grant_id=g.id"
        " where g.id=%s",
        (grant_id,),
    ).fetchone()
    if current is None:
        raise RuntimeError("shared resource grant has no explicit version selection")
    return {
        "status": "duplicate" if duplicate else "active",
        "grant": dict(current),
    }


def list_grants_tx(
    conn: Any, source_workspace_id: str, resource_id: str
) -> list[dict[str, Any]] | None:
    resource = conn.execute(
        "select id from shared_resources where id=%s and owner_workspace_id=%s"
        " and owner_deleted_at is null",
        (resource_id, source_workspace_id),
    ).fetchone()
    if resource is None:
        return None
    rows = conn.execute(
        "select g.*, w.name as workspace_name from shared_resource_grants g"
        " join workspaces w on w.id=g.workspace_id"
        " where g.resource_id=%s order by g.created_at, g.workspace_id",
        (resource_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def revoke_grant_tx(
    conn: Any,
    *,
    source_workspace_id: str,
    resource_id: str,
    target_workspace_id: str,
    actor_id: str,
) -> dict[str, Any] | None:
    resource = conn.execute(
        "select id from shared_resources where id=%s and owner_workspace_id=%s"
        " and owner_deleted_at is null",
        (resource_id, source_workspace_id),
    ).fetchone()
    if resource is None:
        return None
    grant = conn.execute(
        "select * from shared_resource_grants where resource_id=%s and workspace_id=%s for update",
        (resource_id, target_workspace_id),
    ).fetchone()
    if grant is None:
        return None
    if str(grant["status"]) == "active":
        conn.execute(
            "update shared_resource_grants set status='revoked', revoked_by=%s,"
            " revoked_at=current_timestamp where id=%s and status='active'",
            (actor_id, grant["id"]),
        )
    current = conn.execute(
        "select * from shared_resource_grants where id=%s", (grant["id"],)
    ).fetchone()
    return dict(current) if current is not None else None


def select_version_tx(
    conn: Any,
    *,
    workspace_id: str,
    resource_id: str,
    version: int,
    expected_revision: int,
    actor_id: str,
) -> dict[str, Any]:
    grant = conn.execute(
        "select * from shared_resource_grants where workspace_id=%s and resource_id=%s for update",
        (workspace_id, resource_id),
    ).fetchone()
    if grant is None:
        return {"status": "not_found"}
    if str(grant["status"]) != "active":
        return {"status": "revoked"}
    selected = conn.execute(
        "select * from shared_resource_versions"
        " where resource_id=%s and version=%s and status='ready' and deleted_at is null"
        " for key share",
        (resource_id, version),
    ).fetchone()
    if selected is None:
        return {"status": "not_found"}
    current = conn.execute(
        "select * from shared_resource_selections where grant_id=%s", (grant["id"],)
    ).fetchone()
    if current is None:
        return {"status": "not_found"}
    current_revision = int(current["revision"])
    if current_revision != expected_revision:
        return {"status": "conflict", "current_revision": current_revision}
    if str(current["version_id"]) == str(selected["id"]):
        row = conn.execute(
            "select s.*, v.version, v.manifest_hash from shared_resource_selections s"
            " join shared_resource_versions v on v.id=s.version_id where s.grant_id=%s",
            (grant["id"],),
        ).fetchone()
        return {"status": "unchanged", "selection": dict(row)}
    conn.execute(
        "update shared_resource_selections set version_id=%s, revision=revision+1,"
        " selected_by=%s, updated_at=current_timestamp where grant_id=%s and revision=%s",
        (selected["id"], actor_id, grant["id"], expected_revision),
    )
    row = conn.execute(
        "select s.*, v.version, v.manifest_hash from shared_resource_selections s"
        " join shared_resource_versions v on v.id=s.version_id where s.grant_id=%s",
        (grant["id"],),
    ).fetchone()
    return {"status": "changed", "selection": dict(row)}


def resolve_version_tx(
    conn: Any,
    *,
    workspace_id: str,
    resource_id: str,
    version: int,
    manifest_hash: str,
    for_new_run: bool,
) -> dict[str, Any]:
    grant = conn.execute(
        "select * from shared_resource_grants where workspace_id=%s and resource_id=%s",
        (workspace_id, resource_id),
    ).fetchone()
    if grant is None:
        return {"status": "not_found"}
    if for_new_run and str(grant["status"]) != "active":
        return {"status": "revoked"}
    row = conn.execute(
        "select * from shared_resource_versions where resource_id=%s and version=%s"
        " and status='ready'",
        (resource_id, version),
    ).fetchone()
    if row is None or (for_new_run and row["deleted_at"] is not None):
        return {"status": "not_found"}
    if str(row["manifest_hash"]) != manifest_hash:
        return {"status": "hash_mismatch"}
    intent = version_with_files_tx(conn, str(row["id"]))
    if intent is None:
        return {"status": "not_found"}
    return {"status": "ready", "grant": dict(grant), **intent}


def delete_version_tx(
    conn: Any,
    *,
    owner_workspace_id: str,
    resource_id: str,
    version: int,
) -> dict[str, Any]:
    resource = conn.execute(
        "select id from shared_resources where id=%s and owner_workspace_id=%s"
        " and owner_deleted_at is null",
        (resource_id, owner_workspace_id),
    ).fetchone()
    if resource is None:
        return {"status": "not_found"}
    row = conn.execute(
        "select * from shared_resource_versions"
        " where resource_id=%s and version=%s and status='ready' and deleted_at is null"
        " for update",
        (resource_id, version),
    ).fetchone()
    if row is None:
        return {"status": "not_found"}
    version_id = str(row["id"])
    selections = conn.execute(
        "select count(*) as count from shared_resource_selections where version_id=%s",
        (version_id,),
    ).fetchone()
    pins = conn.execute(
        "select count(*) as count from shared_resource_job_pins where version_id=%s",
        (version_id,),
    ).fetchone()
    selection_count = int(selections["count"])
    job_pin_count = int(pins["count"])
    if selection_count or job_pin_count:
        return {
            "status": "in_use",
            "selection_references": selection_count,
            "job_pin_references": job_pin_count,
        }
    conn.execute(
        "update shared_resource_versions set deleted_at=current_timestamp where id=%s"
        " and status='ready' and deleted_at is null",
        (version_id,),
    )
    return {
        "status": "deleted",
        "version_id": version_id,
        "selection_references": 0,
        "job_pin_references": 0,
    }
