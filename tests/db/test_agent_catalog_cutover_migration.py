"""Schema v27: agent_definitions 表删除 + published pi Agent 翻转为 velites。"""

from __future__ import annotations

import hashlib
import json

import pytest

from server.app.db.migrations import migrate_agent_catalog_cutover
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _seed_agent_version(conn, agent_id: str, version: int, status: str, runtime: str) -> None:
    definition = {"capability": f"cap-{agent_id}", "runtime": runtime, "skill": "q/a"}
    conn.execute(
        "insert into versioned_entities("
        "id, entity_type, workspace_id, entity_key, version, status,"
        " definition_json, definition_hash, created_by)"
        " values (%s, 'agent', null, %s, %s, %s, %s, %s, 'user:test')",
        (
            f"agent:{agent_id}:v{version}",
            agent_id,
            version,
            status,
            json.dumps(definition),
            f"hash-{agent_id}-v{version}",
        ),
    )


def _agent_rows(conn, agent_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "select entity_key, version, status, definition_json, definition_hash"
            " from versioned_entities where entity_key=%s order by version",
            (agent_id,),
        ).fetchall()
    ]


@pytest.mark.fresh_schema
def test_cutover_drops_legacy_table_and_flips_pi_agents() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            create table if not exists agent_definitions (
              agent_id text primary key,
              capability text not null,
              runtime text not null,
              definition_json text not null,
              definition_hash text not null,
              enabled integer not null default 1 check(enabled in (0, 1)),
              updated_at timestamptz not null default current_timestamp
            )
            """
        )
        _seed_agent_version(conn, "cutover-pi", 1, "published", "pi")
        _seed_agent_version(conn, "cutover-velites", 1, "published", "velites")
        migrate_agent_catalog_cutover(conn)

        dropped = conn.execute("select to_regclass('agent_definitions') as t").fetchone()
        assert dropped["t"] is None

        pi_rows = _agent_rows(conn, "cutover-pi")
        assert [(row["version"], row["status"]) for row in pi_rows] == [
            (1, "archived"),
            (2, "published"),
        ]
        flipped = json.loads(pi_rows[1]["definition_json"])
        assert flipped["runtime"] == "velites"
        assert flipped["capability"] == "cap-cutover-pi"
        # 新 hash 与 canonical JSON 一致（dispatch 冻结的 hash 能对上 published 行）。
        expected = json.dumps(flipped, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert pi_rows[1]["definition_hash"] == hashlib.sha256(expected.encode()).hexdigest()

        # 非 pi 的 published Agent 不动。
        velites_rows = _agent_rows(conn, "cutover-velites")
        assert [(row["version"], row["status"]) for row in velites_rows] == [(1, "published")]

        # 幂等重放：无新增版本。
        total = conn.execute("select count(*) as c from versioned_entities").fetchone()["c"]
        migrate_agent_catalog_cutover(conn)
        total_after = conn.execute("select count(*) as c from versioned_entities").fetchone()["c"]
    assert total_after == total


@pytest.mark.fresh_schema
def test_cutover_flip_keeps_single_published_row() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select entity_key, count(*) as c from versioned_entities"
            " where entity_type='agent' and status='published'"
            " group by entity_key having count(*) > 1"
        ).fetchall()
    assert rows == []
