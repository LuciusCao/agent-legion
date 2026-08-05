"""Schema v15: legacy workspace cms_config folds into resource bindings."""

from __future__ import annotations

import json
from typing import Any

import pytest

from server.app.db.migrations import migrate_workspace_cms_config
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

# These tests ALTER TABLE workspaces (legacy cms_config_json column) and never
# drop it; TRUNCATE-based isolation would leak the column into later tests.
pytestmark = pytest.mark.fresh_schema


def _add_legacy_column(conn: Any) -> None:
    conn.execute(
        "alter table workspaces add column if not exists cms_config_json text not null default '{}'"
    )


def _insert_workspace(
    conn: Any,
    workspace_id: str,
    cms_config: dict[str, Any],
    resource_config: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "insert into workspaces(id, name, cms_config_json, resource_config_json)"
        " values (%s, %s, %s, %s)",
        (
            workspace_id,
            workspace_id,
            json.dumps(cms_config, sort_keys=True),
            json.dumps(resource_config or {}, sort_keys=True),
        ),
    )


def _resource_config(conn: Any, workspace_id: str) -> dict[str, Any]:
    row = conn.execute(
        "select resource_config_json from workspaces where id=%s", (workspace_id,)
    ).fetchone()
    return json.loads(row["resource_config_json"])


def test_migration_maps_legacy_cms_config_into_bindings() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _add_legacy_column(conn)
        _insert_workspace(
            conn,
            "legacy-ws",
            {
                "question_detail_url": "http://cms.example/detail",
                "question_list_url": "http://cms.example/list",
                "token": "secret-token",
                "env": "prod",
                "bank_version": "v5",
                "country_id": "1",
                "subject_id": "2",
                "page_size": "50",
            },
        )
        migrate_workspace_cms_config(conn)
        resources = _resource_config(conn, "legacy-ws")["resources"]

    detail = resources["question_detail"]
    assert detail["enabled"] is True
    assert detail["config"]["api_url"] == "http://cms.example/detail"
    assert detail["config"]["token"] == "secret-token"
    assert detail["config"]["env"] == "prod"
    assert detail["config"]["bank_version"] == "v5"
    assert "page_size" not in detail["config"]

    listing = resources["by_knowledge"]
    assert listing["enabled"] is True
    assert listing["config"]["api_url"] == "http://cms.example/list"
    assert listing["config"]["page_size"] == 50


def test_migration_maps_generic_api_url_to_both_resources() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _add_legacy_column(conn)
        _insert_workspace(conn, "generic-ws", {"api_url": "http://cms.example/custom"})
        migrate_workspace_cms_config(conn)
        resources = _resource_config(conn, "generic-ws")["resources"]

    assert resources["question_detail"]["config"]["api_url"] == "http://cms.example/custom"
    assert resources["by_knowledge"]["config"]["api_url"] == "http://cms.example/custom"


def test_migration_preserves_existing_binding_values() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _add_legacy_column(conn)
        _insert_workspace(
            conn,
            "bound-ws",
            {"question_detail_url": "http://legacy.example/detail", "subject_id": "9"},
            resource_config={
                "resources": {
                    "question_detail": {
                        "enabled": False,
                        "config": {"api_url": "http://new.example/detail"},
                    }
                }
            },
        )
        migrate_workspace_cms_config(conn)
        binding = _resource_config(conn, "bound-ws")["resources"]["question_detail"]

    assert binding["enabled"] is False
    assert binding["config"]["api_url"] == "http://new.example/detail"
    assert binding["config"]["subject_id"] == "9"


def test_migration_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _add_legacy_column(conn)
        _insert_workspace(conn, "idem-ws", {"api_url": "http://cms.example/detail", "token": "t"})
        migrate_workspace_cms_config(conn)
        first = _resource_config(conn, "idem-ws")
        migrate_workspace_cms_config(conn)
        assert _resource_config(conn, "idem-ws") == first


def test_migration_skips_empty_legacy_config() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _add_legacy_column(conn)
        _insert_workspace(conn, "empty-ws", {})
        migrate_workspace_cms_config(conn)
        assert _resource_config(conn, "empty-ws") == {}


def test_schema_v15_drops_legacy_column() -> None:
    # The autouse fixture already ran init_db at SCHEMA_VERSION 15.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='workspaces'"
            ).fetchall()
        }
    assert "cms_config_json" not in columns
