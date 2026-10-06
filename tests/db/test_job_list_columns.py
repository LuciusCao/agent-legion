"""#957: list_jobs 投影——jobs 全部列减去 KB 级 intake 载荷。

钉住两件事：投影恰为「jobs 现有列 − {input_json, frozen_config_json}」
（新增 jobs 列必须在 JOB_LIST_COLUMNS 里显式决定去留），以及 list_jobs
返回的其余字段值与 ``select *`` 逐列相等。
"""

from __future__ import annotations

from server.app.jobs.queries.job_list_columns import JOB_LIST_COLUMNS

_EXCLUDED = {"input_json", "frozen_config_json"}


def _projection() -> list[str]:
    return [column.strip() for column in JOB_LIST_COLUMNS.split(",")]


def test_projection_is_every_jobs_column_except_intake_payloads(job_db) -> None:
    with job_db._connect_read() as conn:
        columns = {
            str(r["column_name"])
            for r in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema = current_schema() and table_name = 'jobs'"
            )
        }
    projection = _projection()
    assert len(projection) == len(set(projection))
    assert set(projection) == columns - _EXCLUDED


def test_list_jobs_rows_equal_select_star_minus_intake_payloads(job_db) -> None:
    with job_db.connect() as conn:
        conn.execute("insert into workspaces(id, name) values ('cols-ws', 'C')")
        for index in range(3):
            conn.execute(
                "insert into jobs(id, workspace_id, source_type, source_id, run_id, title,"
                " input_json, frozen_config_json, workflow_definition_snapshot_json,"
                " workflow_version) values (%s, 'cols-ws', 'q', %s, 'run-1', %s, %s, %s, %s, %s)",
                (
                    f"job-{index}",
                    f"src-{index}",
                    f"title {index}",
                    '{"text": "' + "x" * 4096 + '"}',
                    '{"node": {}}',
                    '{"nodes": []}',
                    index,
                ),
            )
        full = {
            str(r["id"]): dict(r)
            for r in conn.execute("select * from jobs where workspace_id='cols-ws'")
        }

    listed = job_db.list_jobs(workspace_id="cols-ws", run_id="run-1")

    assert sorted(row["id"] for row in listed) == sorted(full)
    for row in listed:
        expected = {k: v for k, v in full[row["id"]].items() if k not in _EXCLUDED}
        assert row == expected
