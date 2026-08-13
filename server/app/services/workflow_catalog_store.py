"""DB-backed workflow catalog (schema v40, DB-WORKFLOW-CATALOG-001).

The catalog replaces the hardcoded ``workflows.builtin`` registry as the
runtime source of known workflow keys: routes, workspace binding validation,
and the workflow worker's scan list all read the ``workflow_catalog`` table.
Built-in rows (origin='builtin') are upserted from the code constants on every
startup — the repo stays the seed source of truth, so a code update refreshes
the seeded rows while admin-registered rows (origin='registered') are never
touched by seeding. A registered entry starts without a definition: binding a
workspace to it is allowed, and the first workspace draft publish creates
revision v1 through the existing publish channel.
"""

from __future__ import annotations

import json
import re
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.workflow_revision_format import workflow_definition_to_response_payload
from server.app.settings import Settings
from server.app.workflows.builtin import BUILTIN_WORKFLOW_DEFINITIONS
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_KEY_MAX_LENGTH = 64

_ENTRY_COLUMNS = "key, label, description, origin, definition_json"


class WorkflowCatalogStore:
    """Raw ``workflow_catalog`` row access."""

    def __init__(self, database_dsn: DatabaseDsn) -> None:
        self._dsn = database_dsn

    def list_entries(self) -> list[dict[str, Any]]:
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                f"select {_ENTRY_COLUMNS} from workflow_catalog order by key"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_entry(self, workflow_key: str) -> dict[str, Any] | None:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                f"select {_ENTRY_COLUMNS} from workflow_catalog where key=%s",
                (workflow_key,),
            ).fetchone()
        return None if row is None else dict(row)

    def upsert_builtin(self, *, key: str, label: str, definition_json: str) -> None:
        """Insert or refresh a built-in row; registered rows are left alone."""
        with write_transaction(self._dsn) as conn:
            conn.execute(
                """
                insert into workflow_catalog(key, label, origin, definition_json)
                values (%s, %s, 'builtin', %s)
                on conflict(key) do update set
                  label=excluded.label,
                  definition_json=excluded.definition_json,
                  updated_at=current_timestamp
                where workflow_catalog.origin='builtin'
                """,
                (key, label, definition_json),
            )

    def insert_registered(self, *, key: str, label: str, description: str) -> dict[str, Any] | None:
        """Insert a registered row; None when the key already exists."""
        with write_transaction(self._dsn) as conn:
            row = conn.execute(
                f"""
                insert into workflow_catalog(key, label, description, origin)
                values (%s, %s, %s, 'registered')
                on conflict(key) do nothing
                returning {_ENTRY_COLUMNS}
                """,
                (key, label, description),
            ).fetchone()
        return None if row is None else dict(row)


def seed_builtin_workflow_catalog(database_dsn: DatabaseDsn) -> None:
    """Upsert every built-in workflow from the code registry (idempotent)."""
    store = WorkflowCatalogStore(database_dsn)
    for key, raw in BUILTIN_WORKFLOW_DEFINITIONS.items():
        store.upsert_builtin(
            key=key,
            label=str(raw.get("label") or key),
            # Declaration order matters (DAG node order is presentation order),
            # so no sort_keys here.
            definition_json=json.dumps(raw, ensure_ascii=False),
        )


def _summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": str(entry["key"]),
        "label": str(entry["label"]),
        "description": str(entry["description"]),
        "origin": str(entry["origin"]),
    }


class WorkflowCatalogService:
    """Catalog reads for routes/services plus the admin registration write."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._store = WorkflowCatalogStore(settings.database_url)

    @staticmethod
    def seed_builtin(database_dsn: DatabaseDsn) -> None:
        """Startup hook: upsert built-in catalog rows from the code registry."""
        seed_builtin_workflow_catalog(database_dsn)

    def entry(self, workflow_key: str) -> dict[str, Any] | None:
        return self._store.get_entry(workflow_key)

    def require_entry(self, workflow_key: str) -> dict[str, Any]:
        entry = self._store.get_entry(workflow_key)
        if entry is None:
            raise NotFoundError("Unknown workflow")
        return entry

    def label_of(self, workflow_key: str) -> str:
        """Catalog label, falling back to the raw key for unknown workflows."""
        entry = self._store.get_entry(workflow_key)
        return str(entry["label"]) if entry is not None else workflow_key

    def definition_or_none(self, workflow_key: str) -> WorkflowDefinition | None:
        entry = self._store.get_entry(workflow_key)
        raw = entry.get("definition_json") if entry is not None else None
        return workflow_definition_from_dict(json.loads(str(raw))) if raw else None

    def bound_definition(self, workflow_key: str) -> WorkflowDefinition | None:
        """Definition for a workspace bind: 404 unknown key, None when the
        registered workflow has no seed definition (first draft publish
        creates revision v1)."""
        self.require_entry(workflow_key)
        return self.definition_or_none(workflow_key)

    def definition(self, workflow_key: str) -> WorkflowDefinition:
        entry = self.require_entry(workflow_key)
        raw = entry.get("definition_json")
        if not raw:
            raise NotFoundError(f"Workflow {workflow_key!r} has no published definition yet")
        return workflow_definition_from_dict(json.loads(str(raw)))

    def list_workflows(self) -> list[dict[str, Any]]:
        return [_summary(entry) for entry in self._store.list_entries()]

    def workflow(self, workflow_key: str) -> dict[str, Any]:
        return workflow_definition_to_response_payload(self.definition(workflow_key))

    def register(self, workflow_key: str, label: str, description: str = "") -> dict[str, Any]:
        """Register a new workflow key (origin='registered', no definition)."""
        key = workflow_key.strip()
        if not _KEY_PATTERN.match(key) or len(key) > _KEY_MAX_LENGTH:
            raise InvalidOperationError("Workflow key must match ^[a-z][a-z0-9_]*$ (max 64 chars)")
        clean_label = label.strip()
        if not clean_label:
            raise InvalidOperationError("Workflow label must be non-empty")
        entry = self._store.insert_registered(
            key=key, label=clean_label, description=description.strip()
        )
        if entry is None:
            raise ConflictError(f"Workflow {key!r} is already registered")
        return _summary(entry)
