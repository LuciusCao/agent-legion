"""create_jobs_bulk set-based INSERT (issue #448 phase 1, #441 codex review).

The executemany shape was N independent INSERT statements server-side, so
the v77 statement triggers fired once per jobs row — one counter upsert per
row, the exact per-row cost the statement-level migration meant to remove.
The unnest rewrite batches a whole run's inserts into one statement per
1000 rows; these tests pin:

- large-batch correctness: 10k rows land with both counter families
  (run + workspace) exactly equal to the group-by truth, across the
  batching boundary (1000-row chunks);
- return-value equivalence: the returned rows keep the executemany
  contract (one row per candidate, in candidate order, with the
  workflow_key identity shim);
- re-submission semantics: the ON CONFLICT update arm still rebinds
  run_id/title/input/frozen config on existing rows;
- trigger-count economics: a full-batch insert fires the statement
  trigger once per batch, not once per row.
"""

from __future__ import annotations

import json
from pathlib import Path

from server.app.db.transaction import read_connection
from server.app.jobs.queries import JobQueries
from tests.postgres_support import TEST_DATABASE_URL

_REVISION = {
    "id": "rev-sb-1",
    "version": 3,
    "definition_hash": "hash-sb",
    "definition_json": '{"nodes": {}}',
}
_NODE_KEYS = ["node_a", "node_b"]
# Above the 1000-row batch boundary so the chunking path is exercised.
_ROW_COUNT = 10_000


def _candidate(index: int) -> dict[str, object]:
    return {
        "entity_id": f"item-{index:05d}",
        "entity_type": "question",
        "title": f"Title {index}",
        "stem": f"Stem {index}",
    }


def _make_db(tmp_path: Path) -> JobQueries:
    return JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")


def _seed_workspace(db: JobQueries, workspace_id: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values (%s, %s, 'demo_workflow') on conflict (id) do nothing",
            (workspace_id, workspace_id),
        )
        conn.execute(
            "insert into runs(id, workspace_id, source_kind)"
            " values (%s, %s, 'items') on conflict (id) do nothing",
            ("run-sb-1", workspace_id),
        )


def _counter_map(rows: list) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in rows}


def test_bulk_create_lands_counters_equal_to_group_by(tmp_path: Path) -> None:
    # The load-bearing property (#437/#448): however the statement triggers
    # fire, the run and workspace counter tables must equal the plain
    # group-by over jobs after a large batched insert.
    db = _make_db(tmp_path)
    _seed_workspace(db, "ws-sb")

    db.create_jobs_bulk(
        candidates=[_candidate(i) for i in range(_ROW_COUNT)],
        workflow_key="wf",
        run_id="run-sb-1",
        node_keys=_NODE_KEYS,
        workspace_id="ws-sb",
        revision=_REVISION,
        frozen_config={"node_a": {"k": "v"}},
    )

    with read_connection(TEST_DATABASE_URL) as conn:
        truth = conn.execute(
            "select status, count(*) from jobs where run_id=%s group by status",
            ("run-sb-1",),
        ).fetchall()
        run_counts = conn.execute(
            "select status, cnt from run_job_status_counts where run_id=%s",
            ("run-sb-1",),
        ).fetchall()
        ws_counts = conn.execute(
            "select status, cnt from workspace_job_status_counts where workspace_id=%s",
            ("ws-sb",),
        ).fetchall()
        total = conn.execute("select count(*) from jobs where run_id=%s", ("run-sb-1",)).fetchone()
        nodes = conn.execute(
            "select count(*) from job_nodes j join jobs s on s.id=j.job_id where s.run_id=%s",
            ("run-sb-1",),
        ).fetchone()

    assert int(total["count"]) == _ROW_COUNT
    assert _counter_map(run_counts) == _counter_map(truth)
    assert _counter_map(ws_counts) == _counter_map(truth)
    assert _counter_map(truth) == {"queued": _ROW_COUNT}
    # job_nodes: every job × every node key, exactly once.
    assert int(nodes["count"]) == _ROW_COUNT * len(_NODE_KEYS)


def test_bulk_create_returns_rows_in_candidate_order_with_identity_shim(tmp_path: Path) -> None:
    # Executemany equivalence: the return value keeps one row per candidate
    # in input order, and the deprecated workflow_key field carries the
    # workspace identity value (#211 M2 shim).
    db = _make_db(tmp_path)
    _seed_workspace(db, "ws-sb")

    jobs = db.create_jobs_bulk(
        candidates=[_candidate(i) for i in range(3)],
        workflow_key="wf",
        run_id="run-sb-1",
        node_keys=_NODE_KEYS,
        workspace_id="ws-sb",
        revision=_REVISION,
        frozen_config={},
    )

    assert [str(job["id"]) for job in jobs] == [
        "ws-sb_wf_item-00000",
        "ws-sb_wf_item-00001",
        "ws-sb_wf_item-00002",
    ]
    assert all(str(job["workflow_key"]) == "ws-sb" for job in jobs)
    assert all(str(job["run_id"]) == "run-sb-1" for job in jobs)
    assert json.loads(str(jobs[0]["input_json"]))["external_id"] == "item-00000"


def test_bulk_resubmit_rebinds_run_and_freeze(tmp_path: Path) -> None:
    # The ON CONFLICT arm: a re-submitted job takes the new run binding,
    # title, input and frozen config; its job_nodes rows survive (do
    # nothing), and the run counters follow the rebind.
    db = _make_db(tmp_path)
    _seed_workspace(db, "ws-sb")
    with db.connect() as conn:
        conn.execute(
            "insert into runs(id, workspace_id, source_kind)"
            " values (%s, %s, 'items') on conflict (id) do nothing",
            ("run-sb-2", "ws-sb"),
        )

    db.create_jobs_bulk(
        candidates=[_candidate(0)],
        workflow_key="wf",
        run_id="run-sb-1",
        node_keys=_NODE_KEYS,
        workspace_id="ws-sb",
        revision=_REVISION,
        frozen_config={"node_a": {"k": "old"}},
    )
    jobs = db.create_jobs_bulk(
        candidates=[_candidate(0)],
        workflow_key="wf",
        run_id="run-sb-2",
        node_keys=_NODE_KEYS,
        workspace_id="ws-sb",
        revision=_REVISION,
        frozen_config={"node_a": {"k": "new"}},
    )

    job = jobs[0]
    assert str(job["run_id"]) == "run-sb-2"
    assert str(job["title"]) == "Title 0"
    assert json.loads(str(job["frozen_config_json"])) == {"node_a": {"k": "new"}}
    with read_connection(TEST_DATABASE_URL) as conn:
        nodes = conn.execute(
            "select count(*) as cnt from job_nodes where job_id=%s", ("ws-sb_wf_item-00000",)
        ).fetchone()
        run1 = conn.execute(
            "select cnt from run_job_status_counts where run_id=%s and status='queued'",
            ("run-sb-1",),
        ).fetchone()
        run2 = conn.execute(
            "select cnt from run_job_status_counts where run_id=%s and status='queued'",
            ("run-sb-2",),
        ).fetchone()
    # No duplicate node rows from the double submit.
    assert int(nodes["cnt"]) == len(_NODE_KEYS)
    # The rebind moved the row from run 1 to run 2 in the counters.
    assert run1 is None or int(run1["cnt"]) == 0
    assert run2 is not None and int(run2["cnt"]) == 1


def test_bulk_duplicate_ids_in_one_call_take_the_last_row(tmp_path: Path) -> None:
    # #461 review: two rows with the same job id inside ONE unnest batch are
    # a CardinalityViolation (set-based ON CONFLICT), where the old
    # executemany shape let the later row's DO UPDATE win over the earlier
    # one. create_jobs_bulk must dedup by id before the SQL — last row wins
    # — restoring those semantics.
    db = _make_db(tmp_path)
    _seed_workspace(db, "ws-sb")

    first = dict(_candidate(0), title="First Title")
    second = dict(_candidate(0), title="Second Title")
    jobs = db.create_jobs_bulk(
        candidates=[_candidate(1), first, second],
        workflow_key="wf",
        run_id="run-sb-1",
        node_keys=_NODE_KEYS,
        workspace_id="ws-sb",
        revision=_REVISION,
        frozen_config={},
    )

    # One row per unique id, in first-seen order; the duplicate carries the
    # LATER row's values (executemany's later-DO-UPDATE-wins semantics).
    assert [str(job["id"]) for job in jobs] == [
        "ws-sb_wf_item-00001",
        "ws-sb_wf_item-00000",
    ]
    assert str(jobs[1]["title"]) == "Second Title"
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select count(*) as cnt from jobs where run_id=%s", ("run-sb-1",)
        ).fetchone()
        nodes = conn.execute(
            "select count(*) as cnt from job_nodes where job_id=%s",
            ("ws-sb_wf_item-00000",),
        ).fetchone()
    assert int(rows["cnt"]) == 2
    # The duplicate id did not double-insert its node rows either.
    assert int(nodes["cnt"]) == len(_NODE_KEYS)


def test_bulk_insert_fires_statement_trigger_once_per_batch(tmp_path: Path) -> None:
    # The #441 review's point: with executemany each jobs row was its own
    # statement and its own trigger firing. The unnest shape must fire the
    # v77 INSERT trigger once per batch. Measured via pg_stat_user_tables
    # delta around a 2500-row create (3 batches: 1000+1000+500) on a fresh
    # run so the first batch INSERTS the counter row (n_tup_ins) and the
    # later batches take the ON CONFLICT UPDATE arm (n_tup_upd) — the
    # firing count is the ins+upd delta, not upd alone. Two measurement
    # realities (#461 review): pg_stat counters land asynchronously (~1s
    # behind the writer's commit locally), so poll until the expected
    # firings appear instead of trusting one fixed sleep; and the counter
    # row for this run must not pre-exist, or every batch takes the update
    # arm and the insert delta reads 0.
    import time

    db = _make_db(tmp_path)
    _seed_workspace(db, "ws-sb2")
    with db.connect() as conn:
        conn.execute("delete from jobs where run_id='run-sb-1'")
        conn.execute("delete from run_job_status_counts where run_id='run-sb-1'")
    with read_connection(TEST_DATABASE_URL) as conn:
        before = conn.execute(
            "select n_tup_ins, n_tup_upd from pg_stat_user_tables"
            " where schemaname=current_schema() and relname='run_job_status_counts'"
        ).fetchone()
    assert before is not None
    base_ins = int(before["n_tup_ins"])
    base_upd = int(before["n_tup_upd"])

    db.create_jobs_bulk(
        candidates=[_candidate(i) for i in range(2500)],
        workflow_key="wf",
        run_id="run-sb-1",
        node_keys=["node_a"],
        workspace_id="ws-sb2",
        revision=_REVISION,
    )

    # Stats are async: pg_stat_force_next_flush from this backend asks the
    # writer's collector to flush, and the counters land up to ~1s later
    # (measured locally) — poll for the firings instead of trusting one
    # fixed read. The counter-equality test above remains the hard
    # correctness check; this is the trigger-economics pin.
    firings = 0
    for _ in range(30):
        time.sleep(0.1)
        with read_connection(TEST_DATABASE_URL) as conn:
            conn.execute("select pg_stat_force_next_flush()")
            after = conn.execute(
                "select n_tup_ins, n_tup_upd from pg_stat_user_tables"
                " where schemaname=current_schema() and relname='run_job_status_counts'"
            ).fetchone()
        assert after is not None
        firings = (int(after["n_tup_ins"]) - base_ins) + (int(after["n_tup_upd"]) - base_upd)
        if firings >= 3:
            break
    # 2500 rows in 1000-row batches = 3 statement firings (1000+1000+500),
    # each upserting one counter row for this run — nowhere near the
    # per-row count the executemany shape produced. The lower bound pins
    # that every batch actually fired (at least one counter write per
    # batch); the upper bound keeps the assertion robust against concurrent
    # stats noise from other tests' writes to the same shared stats table.
    assert 3 <= firings <= 3 + 10, firings
