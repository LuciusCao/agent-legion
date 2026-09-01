"""Preview panel entity type (schema v71, issue #328).

Widens the ``versioned_entities`` entity_type CHECK so workspace-scoped
preview panel bundles (job detail left-column custom preview, HTML+CSS+JS
single file) join the unified draft → published → archived lifecycle. Same
drop + re-add pattern as v30/v47; idempotent on replay. No data migration:
the entity type is new, so no existing rows need rewriting.
"""

from __future__ import annotations

from typing import Any

_ENTITY_TYPE_CHECK_DDL = """
alter table versioned_entities
  drop constraint if exists versioned_entities_entity_type_check;
alter table versioned_entities
  add constraint versioned_entities_entity_type_check
  check(entity_type in ('node_code', 'agent', 'preview_panel'))
"""


def migrate_preview_panels(conn: Any) -> None:
    """Allow entity_type 'preview_panel' on versioned_entities (v71); idempotent."""
    conn.execute(_ENTITY_TYPE_CHECK_DDL)
