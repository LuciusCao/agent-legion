"""Schema v104 batch approval fields and kind vocabulary (#1363)."""

from __future__ import annotations

from server.app.db.migrations.approval_batch import migrate_approval_batch
from server.app.jobs import JobQueries


def test_approval_batch_columns_and_kinds(job_db: JobQueries) -> None:
    with job_db.connect() as conn:
        rows = conn.execute(
            "select column_name from information_schema.columns"
            " where table_name='approval_decisions'"
            " and column_name in ('payload_json', 'batch_id', 'batch_selection')"
        ).fetchall()
        assert {row["column_name"] for row in rows} == {
            "payload_json",
            "batch_id",
            "batch_selection",
        }
        migrate_approval_batch(conn)

    workspace = job_db.create_workspace(name="approval-schema-ws")
    for kind in ("approve", "reject"):
        op_id = f"op-{kind}"
        job_db.create_job_batch_operation(
            op_id, str(workspace["id"]), kind, {"job_ids": []}, 0, "user:tester"
        )
        assert job_db.get_job_batch_operation(op_id)["kind"] == kind
