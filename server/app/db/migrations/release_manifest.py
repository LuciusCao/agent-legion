"""Schema v108 (#120): append-only workspace release manifests.

The v108 backfill freezes the packaging scope that existed at migration time:
the three legacy generic files plus every output declared by the workspace's
active revision. Workspaces created afterwards have no rows and therefore use
the implicit, empty revision 0. Nothing here follows later active revisions.

The migration owns the DDL so fresh and upgraded databases take the same path.
Stored revisions reject updates and deletes; the delete arm only permits the
workspace row's own ON DELETE CASCADE.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from server.app.workflows.loader import workflow_definition_from_dict

_LEGACY_PACKAGE_FILES = frozenset({"result.json", "metadata.json", "report.md"})
_BACKFILL_ACTOR = "schema-v108-backfill"

_DDL = """
create table if not exists workspace_release_manifests (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  revision integer not null check(revision > 0),
  artifact_names_json text not null,
  manifest_hash text not null,
  created_by text not null,
  created_at timestamptz not null default current_timestamp,
  unique (workspace_id, revision)
);

create or replace function workspace_release_manifest_immutable()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' and not exists (
    select 1 from workspaces where id = old.workspace_id
  ) then
    return old;
  end if;
  raise exception 'workspace release manifest revisions are immutable';
end;
$$;

drop trigger if exists workspace_release_manifests_immutable
  on workspace_release_manifests;
create trigger workspace_release_manifests_immutable
  before update or delete on workspace_release_manifests
  for each row execute function workspace_release_manifest_immutable();
"""


def _backfill_names(definition_json: str | None) -> list[str]:
    names = set(_LEGACY_PACKAGE_FILES)
    if definition_json is not None:
        definition = workflow_definition_from_dict(json.loads(definition_json))
        for node in definition.nodes.values():
            names.update(node.outputs)
    return sorted(names)


def _document(names: list[str]) -> str:
    return json.dumps(names, ensure_ascii=False, separators=(",", ":"))


def _hash(names: list[str]) -> str:
    return hashlib.sha256(_document(names).encode("utf-8")).hexdigest()


def migrate_release_manifest(conn: Any) -> None:
    """Create the manifest history and backfill pre-v108 workspaces once."""
    conn.execute(_DDL)
    workspaces = conn.execute("select id from workspaces order by id").fetchall()
    for workspace in workspaces:
        workspace_id = str(workspace["id"])
        revision = conn.execute(
            "select definition_json from workflow_revisions"
            " where workspace_id=%s and status='active'"
            " order by version desc limit 1",
            (workspace_id,),
        ).fetchone()
        names = _backfill_names(str(revision["definition_json"]) if revision is not None else None)
        document = _document(names)
        conn.execute(
            "insert into workspace_release_manifests("
            "id, workspace_id, revision, artifact_names_json, manifest_hash, created_by"
            ") values (%s, %s, 1, %s, %s, %s)"
            " on conflict(workspace_id, revision) do nothing",
            (
                uuid.uuid5(uuid.NAMESPACE_URL, f"agent-legion:v108:{workspace_id}").hex,
                workspace_id,
                document,
                _hash(names),
                _BACKFILL_ACTOR,
            ),
        )
