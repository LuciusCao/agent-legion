"""Executor entity type (schema v30).

Widens the ``versioned_entities`` entity_type CHECK so executor definitions
(retired ``config/workflow.yaml`` executors section) join the unified
draft → published → archived lifecycle as global entities
(``workspace_id`` NULL). The constraint was declared inline in the table DDL,
so PostgreSQL auto-named it ``versioned_entities_entity_type_check``; drop +
re-add keeps the same name. Idempotent on replay.
"""

from __future__ import annotations

from typing import Any

_ENTITY_TYPE_CHECK_DDL = """
alter table versioned_entities
  drop constraint if exists versioned_entities_entity_type_check;
alter table versioned_entities
  add constraint versioned_entities_entity_type_check
  check(entity_type in ('node_code', 'agent', 'executor'))
"""


def migrate_executor_entity_type(conn: Any) -> None:
    """Allow entity_type 'executor' on versioned_entities (v30); idempotent."""
    conn.execute(_ENTITY_TYPE_CHECK_DDL)
