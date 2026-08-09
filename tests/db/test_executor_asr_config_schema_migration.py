"""Schema v31: published code-default executor 补入 transcribe_video ASR config_schema。"""

from __future__ import annotations

import hashlib
import json

from server.app.db.migrations import migrate_executor_asr_config_schema
from server.app.db.transaction import write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _seed_executor_version(
    conn, definition: dict, version: int = 1, status: str = "published"
) -> None:
    # conftest 已在 TRUNCATE 后播种内置 code-default catalog；清掉再播种测试行。
    conn.execute(
        "delete from versioned_entities where entity_type='executor' and entity_key='code-default'"
    )
    conn.execute(
        "insert into versioned_entities("
        "id, entity_type, workspace_id, entity_key, version, status,"
        " definition_json, definition_hash, created_by)"
        " values (%s, 'executor', null, 'code-default', %s, %s, %s, %s, 'user:test')",
        (
            f"executor:code-default:v{version}",
            version,
            status,
            json.dumps(definition),
            f"hash-v{version}",
        ),
    )


def _executor_rows(conn) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "select version, status, definition_json, definition_hash"
            " from versioned_entities where entity_key='code-default' order by version",
        ).fetchall()
    ]


def _v30_definition(config_schema: dict | None = None) -> dict:
    capability: dict = {"path": "workflow_nodes/video_transcribe.py", "sandbox_network": True}
    if config_schema is not None:
        capability["config_schema"] = config_schema
    return {
        "kind": "code",
        "global_capacity": 16,
        "capabilities": {"transcribe_video": capability},
    }


def test_migration_publishes_schema_as_new_version() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_executor_version(conn, _v30_definition())
        migrate_executor_asr_config_schema(conn)

        rows = _executor_rows(conn)
        assert [(row["version"], row["status"]) for row in rows] == [
            (1, "archived"),
            (2, "published"),
        ]
        migrated = json.loads(rows[1]["definition_json"])
        properties = migrated["capabilities"]["transcribe_video"]["config_schema"]["properties"]
        assert properties["provider"]["enum"] == ["auto", "whisper", "sensevoice"]
        assert properties["provider"]["default"] == "auto"
        assert properties["timeout_seconds"]["default"] == 900
        assert properties["timeout_seconds"]["minimum"] == 1
        # 其余 capability 字段不动。
        assert migrated["capabilities"]["transcribe_video"]["path"] == (
            "workflow_nodes/video_transcribe.py"
        )
        # 新 hash 与 canonical JSON 一致（dispatch 冻结的 hash 能对上 published 行）。
        expected = json.dumps(migrated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert rows[1]["definition_hash"] == hashlib.sha256(expected.encode()).hexdigest()


def test_migration_keeps_existing_schema_keys() -> None:
    """已有 config_schema 键不被覆盖（管理员改版优先），缺失键补入。"""
    custom_schema = {
        "type": "object",
        "properties": {
            "provider": {"type": "string", "default": "whisper"},
        },
    }
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_executor_version(conn, _v30_definition(config_schema=custom_schema))
        migrate_executor_asr_config_schema(conn)

        rows = _executor_rows(conn)
        assert [(row["version"], row["status"]) for row in rows] == [
            (1, "archived"),
            (2, "published"),
        ]
        properties = json.loads(rows[1]["definition_json"])["capabilities"]["transcribe_video"][
            "config_schema"
        ]["properties"]
        assert properties["provider"] == {"type": "string", "default": "whisper"}
        assert properties["timeout_seconds"]["default"] == 900


def test_migration_is_idempotent_and_skips_migrated_rows() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_executor_version(conn, _v30_definition())
        migrate_executor_asr_config_schema(conn)
        total = conn.execute("select count(*) as c from versioned_entities").fetchone()["c"]
        migrate_executor_asr_config_schema(conn)
        total_after = conn.execute("select count(*) as c from versioned_entities").fetchone()["c"]
        assert total_after == total
        rows = _executor_rows(conn)
        assert [(row["version"], row["status"]) for row in rows] == [
            (1, "archived"),
            (2, "published"),
        ]


def test_migration_skips_capability_without_transcribe_video() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_executor_version(conn, {"kind": "code", "global_capacity": 16, "capabilities": {}})
        migrate_executor_asr_config_schema(conn)
        rows = _executor_rows(conn)
    assert [(row["version"], row["status"]) for row in rows] == [(1, "published")]
