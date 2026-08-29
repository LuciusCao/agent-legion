"""Schema v64 data migration: backfill top-level execution defaults.

The retired ``workspaces.default_agent_*`` columns were the execution-config
default source; v64 copies non-empty values into the active revision's
top-level ``execution`` block before the post-chain cleanup drops the
columns. These tests direct-call the migration against a restored pre-v64
column shape (the v64 test database already dropped them), pin idempotent
replay and the explicit-block-wins rule, and verify the dispatch resolution
chain works off the rewritten definition alone.
"""

from __future__ import annotations

import hashlib
import json

from server.app.db.migrations.workspace_execution_defaults import (
    migrate_workspace_execution_defaults,
)
from server.app.db.migrations.workspace_settings_retirement import (
    drop_retired_workspace_setting_columns,
)
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

# Pre-v64 asdict snapshot: node-level execution exists, the top-level
# ``execution`` key does not (the v64 loader addition).
_DEFINITION_WITHOUT_EXECUTION = {
    "edges": [{"condition": None, "source": "start", "target": "gen"}],
    "intake": {"modes": {}},
    "key": "wf_demo",
    "label": "Demo",
    "nodes": {
        "start": {
            "accepted_item_types": ["material", "ref"],
            "after": [],
            "capability": "",
            "config": {},
            "config_schema": {},
            "execution": {"model": "", "provider": "", "prompt": "", "thinking": ""},
            "inputs": [],
            "label": "start",
            "node_type": "start",
            "outputs": [],
            "reduce": None,
            "shard": None,
            "terminal": None,
        },
        "gen": {
            "accepted_item_types": ["material", "ref"],
            "after": [],
            "capability": "generate",
            "config": {},
            "config_schema": {},
            "execution": {"model": "", "provider": "", "prompt": "", "thinking": ""},
            "inputs": [],
            "label": "gen",
            "node_type": "node",
            "outputs": [],
            "reduce": None,
            "shard": None,
            "terminal": None,
        },
    },
    "schema_version": 2,
}


def _restore_pre_v64_columns(conn) -> None:
    for column in (
        "default_agent_provider",
        "default_agent_model",
        "default_agent_thinking",
    ):
        conn.execute(
            f"alter table workspaces add column if not exists {column} text not null default ''"
        )


def _seed_workspace(conn, workspace_id: str, provider: str, model: str, thinking: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key, default_agent_provider,"
        " default_agent_model, default_agent_thinking)"
        " values (%s, %s, %s, %s, %s, %s)",
        (workspace_id, workspace_id, workspace_id, provider, model, thinking),
    )


def _seed_active_revision(conn, workspace_id: str, definition: dict) -> None:
    definition_json = json.dumps(definition, ensure_ascii=False, sort_keys=True)
    conn.execute(
        "insert into workflow_revisions(id, workspace_id, workflow_key, version, status,"
        " definition_json, definition_hash)"
        " values (%s, %s, %s, 1, 'active', %s, %s)",
        (
            f"rev-{workspace_id}",
            workspace_id,
            workspace_id,
            definition_json,
            hashlib.sha256(definition_json.encode("utf-8")).hexdigest(),
        ),
    )


def _revision_row(workspace_id: str) -> dict:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select definition_json, definition_hash from workflow_revisions where id=%s",
            (f"rev-{workspace_id}",),
        ).fetchone()
    assert row is not None
    return dict(row)


def test_backfills_defaults_into_active_revision_top_level_execution() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _restore_pre_v64_columns(conn)
        _seed_workspace(conn, "v64-backfill-ws", "ws-provider", "ws-model", "high")
        _seed_active_revision(conn, "v64-backfill-ws", _DEFINITION_WITHOUT_EXECUTION)
        migrate_workspace_execution_defaults(conn)
        drop_retired_workspace_setting_columns(conn)

    row = _revision_row("v64-backfill-ws")
    payload = json.loads(row["definition_json"])
    assert payload["execution"] == {
        "provider": "ws-provider",
        "model": "ws-model",
        "thinking": "high",
        "prompt": "",
    }
    assert (
        row["definition_hash"] == hashlib.sha256(row["definition_json"].encode("utf-8")).hexdigest()
    )

    # 三列 drop 后 dispatch 解析链可用：顶层默认经 loader 合并进节点，
    # resolve_execution_block 读到有效值。
    from server.app.agent_broker.dispatch import resolve_execution_block
    from server.app.workflows.definition import workflow_definition_from_dict

    definition = workflow_definition_from_dict(payload)
    assert definition.execution.provider == "ws-provider"
    block = resolve_execution_block(definition.nodes["gen"], "velites")
    assert block["provider"] == "ws-provider"
    assert block["model"] == "ws-model"
    assert block["thinking"] == "high"


def test_replay_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _restore_pre_v64_columns(conn)
        _seed_workspace(conn, "v64-idem-ws", "ws-provider", "ws-model", "")
        _seed_active_revision(conn, "v64-idem-ws", _DEFINITION_WITHOUT_EXECUTION)
        migrate_workspace_execution_defaults(conn)
        migrate_workspace_execution_defaults(conn)
        drop_retired_workspace_setting_columns(conn)

    payload = json.loads(_revision_row("v64-idem-ws")["definition_json"])
    assert payload["execution"] == {
        "provider": "ws-provider",
        "model": "ws-model",
        "thinking": "",
        "prompt": "",
    }


def test_explicit_top_level_execution_wins() -> None:
    definition = {**_DEFINITION_WITHOUT_EXECUTION, "execution": {"provider": "explicit"}}
    with write_transaction(TEST_DATABASE_URL) as conn:
        _restore_pre_v64_columns(conn)
        _seed_workspace(conn, "v64-explicit-ws", "ws-provider", "ws-model", "")
        _seed_active_revision(conn, "v64-explicit-ws", definition)
        migrate_workspace_execution_defaults(conn)
        drop_retired_workspace_setting_columns(conn)

    payload = json.loads(_revision_row("v64-explicit-ws")["definition_json"])
    assert payload["execution"] == {"provider": "explicit"}


def test_workspace_without_active_revision_is_skipped() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _restore_pre_v64_columns(conn)
        _seed_workspace(conn, "v64-orphan-ws", "ws-provider", "", "")
        migrate_workspace_execution_defaults(conn)
        drop_retired_workspace_setting_columns(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select id from workflow_revisions where workspace_id='v64-orphan-ws'"
        ).fetchall()
    assert rows == []


def test_replay_after_column_drop_is_a_noop() -> None:
    """源列已被 post-chain 清理 drop 的库上 replay（模拟升级路径重建的
    形状）必须 no-op，而不是撞 column does not exist。"""
    with write_transaction(TEST_DATABASE_URL) as conn:
        _restore_pre_v64_columns(conn)
        _seed_workspace(conn, "v64-dropped-ws", "ws-provider", "ws-model", "")
        _seed_active_revision(conn, "v64-dropped-ws", _DEFINITION_WITHOUT_EXECUTION)
        drop_retired_workspace_setting_columns(conn)
        migrate_workspace_execution_defaults(conn)

    payload = json.loads(_revision_row("v64-dropped-ws")["definition_json"])
    assert "execution" not in payload
