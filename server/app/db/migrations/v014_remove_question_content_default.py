import sqlite3

from server.app.db.migrations.models import Migration


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _apply(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "workspaces"):
        return

    # The question_content workflow is being removed. Any workspace that still
    # references it is migrated to the only remaining workflow.
    conn.execute(
        """
        update workspaces
        set default_workflow_key = 'question_comprehension_info'
        where default_workflow_key = 'question_content'
        """
    )


MIGRATION = Migration(
    version=14,
    name="remove_question_content_default",
    apply=_apply,
)
