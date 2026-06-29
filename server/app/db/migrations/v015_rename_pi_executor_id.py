import sqlite3

from server.app.db.migrations.models import Migration

_LEGACY_PI_EXECUTOR_IDS = ("pi-default", "pi-video-main")
_TARGET_PI_EXECUTOR_ID = "pi"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _apply(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "workspace_executor_allocations"):
        conn.execute(
            """
            insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)
            select workspace_id, ?, max(concurrency_limit)
            from workspace_executor_allocations
            where executor_id in (?, ?)
            group by workspace_id
            on conflict(workspace_id, executor_id) do update set
              concurrency_limit = max(
                workspace_executor_allocations.concurrency_limit,
                excluded.concurrency_limit
              )
            """,
            (_TARGET_PI_EXECUTOR_ID, *_LEGACY_PI_EXECUTOR_IDS),
        )
        conn.execute(
            """
            delete from workspace_executor_allocations
            where executor_id in (?, ?)
            """,
            _LEGACY_PI_EXECUTOR_IDS,
        )

    if _table_exists(conn, "workspace_node_bindings"):
        conn.execute(
            """
            update workspace_node_bindings
            set executor_id = ?
            where executor_id in (?, ?)
            """,
            (_TARGET_PI_EXECUTOR_ID, *_LEGACY_PI_EXECUTOR_IDS),
        )

    if _table_exists(conn, "executor_leases"):
        conn.execute(
            """
            update executor_leases
            set executor_id = ?
            where executor_id in (?, ?)
            """,
            (_TARGET_PI_EXECUTOR_ID, *_LEGACY_PI_EXECUTOR_IDS),
        )


MIGRATION = Migration(
    version=15,
    name="rename_pi_executor_id",
    apply=_apply,
)
