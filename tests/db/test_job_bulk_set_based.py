"""create_jobs_bulk set-based INSERT (issue #448 phase 1, #441 codex review).

The executemany shape was N independent INSERT statements server-side, so
the v77 statement triggers fired once per jobs row — one counter upsert per
row, the exact per-row cost the statement-level migration meant to remove.
The unnest rewrite batches each create transaction's inserts into one
statement; these tests pin:

- large-batch correctness: 2.5k rows land with both counter families
  (run + workspace) exactly equal to the group-by truth, across the
  create-chunk boundaries;
- return-value equivalence: the returned rows keep the executemany
  contract (one row per unique id, in first-seen order, with the
  workflow_key identity shim);
- re-submission semantics (#735): the ON CONFLICT update arm refreshes
  title/input/frozen config on rows THIS run already owns, while a row
  owned by a DIFFERENT run is skipped (first writer wins — concurrent
  submissions can never steal a job from its run) and drops out of the
  returned ids;
- trigger-count economics: a full-batch insert fires the statement
  trigger once per batch, not once per row.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs.queries import JobQueries
from server.app.jobs.queries.job_bulk_sql import CREATE_CHUNK_ROWS
from tests.postgres_support import TEST_DATABASE_URL

_REVISION = {
    "id": "rev-sb-1",
    "version": 3,
    "definition_hash": "hash-sb",
    "definition_json": '{"nodes": {}}',
}
_NODE_KEYS = ["node_a", "node_b"]
# Above the 1000-row batch boundary so the chunking path is exercised:
# 2500 = 3 batches (1000+1000+500), the same cross-boundary shape the
# trigger-count test below already proves equivalent to the old 10k.
_ROW_COUNT = 2_500


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
            "insert into workspaces(id, name) values (%s, %s) on conflict (id) do nothing",
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

    # #467 A3: the return value is job ids (first-seen order); row fields
    # are asserted from the DB read path.
    job_ids = db.create_jobs_bulk(
        candidates=[_candidate(i) for i in range(3)],
        workflow_key="wf",
        run_id="run-sb-1",
        node_keys=_NODE_KEYS,
        workspace_id="ws-sb",
        revision=_REVISION,
        frozen_config={},
    )

    assert [str(job_id) for job_id in job_ids] == [
        "ws-sb_wf_item-00000",
        "ws-sb_wf_item-00001",
        "ws-sb_wf_item-00002",
    ]
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select id, workspace_id, run_id, input_json from jobs where id = any(%s)",
            (job_ids,),
        ).fetchall()
    by_id = {str(row["id"]): row for row in rows}
    # The deprecated workflow_key identity shim moved to the read paths.
    assert all(str(row["workspace_id"]) == "ws-sb" for row in rows)
    assert all(str(row["run_id"]) == "run-sb-1" for row in rows)
    assert json.loads(str(by_id["ws-sb_wf_item-00000"]["input_json"]))["external_id"] == (
        "item-00000"
    )


def test_bulk_same_run_resubmit_updates_freeze(tmp_path: Path) -> None:
    # The ON CONFLICT ownership clause passes for rows THIS run already owns:
    # a same-run re-submission takes the new title/input/frozen config
    # (RUN-FREEZE-001), keeps the run binding, returns the id, and its
    # job_nodes rows survive (do nothing).
    db = _make_db(tmp_path)
    _seed_workspace(db, "ws-sb")

    db.create_jobs_bulk(
        candidates=[_candidate(0)],
        workflow_key="wf",
        run_id="run-sb-1",
        node_keys=_NODE_KEYS,
        workspace_id="ws-sb",
        revision=_REVISION,
        frozen_config={"node_a": {"k": "old"}},
    )
    job_ids = db.create_jobs_bulk(
        candidates=[_candidate(0)],
        workflow_key="wf",
        run_id="run-sb-1",
        node_keys=_NODE_KEYS,
        workspace_id="ws-sb",
        revision=_REVISION,
        frozen_config={"node_a": {"k": "new"}},
    )

    assert [str(job_id) for job_id in job_ids] == ["ws-sb_wf_item-00000"]
    with read_connection(TEST_DATABASE_URL) as conn:
        job_row = conn.execute(
            "select run_id, title, frozen_config_json from jobs where id=%s", (job_ids[0],)
        ).fetchone()
        nodes = conn.execute(
            "select count(*) as cnt from job_nodes where job_id=%s", ("ws-sb_wf_item-00000",)
        ).fetchone()
        run1 = conn.execute(
            "select cnt from run_job_status_counts where run_id=%s and status='queued'",
            ("run-sb-1",),
        ).fetchone()
    assert str(job_row["run_id"]) == "run-sb-1"
    assert str(job_row["title"]) == "Title 0"
    assert json.loads(str(job_row["frozen_config_json"])) == {"node_a": {"k": "new"}}
    # No duplicate node rows from the double submit; the counter still
    # counts the row exactly once (the update arm moved nothing).
    assert int(nodes["cnt"]) == len(_NODE_KEYS)
    assert run1 is not None and int(run1["cnt"]) == 1


def test_bulk_cross_run_submit_cannot_steal_ownership(tmp_path: Path) -> None:
    # #735 review P1: the pre-fix arm re-bound run_id to the LAST writer, so
    # two concurrent runs racing one item both reported the job while only
    # the later run still owned it. The ownership clause makes first writer
    # win atomically: the second run's INSERT skips the row, its RETURNING
    # drops the id (the response never claims another run's job), the row
    # keeps its original binding/freeze, and no foreign node rows land on it.
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
    # The losing run even carries a DIFFERENT node set (other revision):
    # nothing of it may leak onto the winner's job.
    stolen_ids = db.create_jobs_bulk(
        candidates=[_candidate(0)],
        workflow_key="wf",
        run_id="run-sb-2",
        node_keys=["node_z"],
        workspace_id="ws-sb",
        revision=_REVISION,
        frozen_config={"node_a": {"k": "new"}},
    )

    assert stolen_ids == []
    with read_connection(TEST_DATABASE_URL) as conn:
        job_row = conn.execute(
            "select run_id, title, frozen_config_json from jobs where id=%s",
            ("ws-sb_wf_item-00000",),
        ).fetchone()
        node_keys = conn.execute(
            "select node_key from job_nodes where job_id=%s order by node_key",
            ("ws-sb_wf_item-00000",),
        ).fetchall()
        run1 = conn.execute(
            "select cnt from run_job_status_counts where run_id=%s and status='queued'",
            ("run-sb-1",),
        ).fetchone()
        run2 = conn.execute(
            "select cnt from run_job_status_counts where run_id=%s and status='queued'",
            ("run-sb-2",),
        ).fetchone()
    assert str(job_row["run_id"]) == "run-sb-1"
    assert json.loads(str(job_row["frozen_config_json"])) == {"node_a": {"k": "old"}}
    assert [str(row["node_key"]) for row in node_keys] == sorted(_NODE_KEYS)
    # The counters never moved: the skipped row fired no INSERT/UPDATE
    # transition row, so run 2 counts nothing and run 1 keeps its job.
    assert run1 is not None and int(run1["cnt"]) == 1
    assert run2 is None or int(run2["cnt"]) == 0


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
    job_ids = db.create_jobs_bulk(
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
    assert [str(job_id) for job_id in job_ids] == [
        "ws-sb_wf_item-00001",
        "ws-sb_wf_item-00000",
    ]
    with read_connection(TEST_DATABASE_URL) as conn:
        title = conn.execute(
            "select title from jobs where id=%s", ("ws-sb_wf_item-00000",)
        ).fetchone()
        rows = conn.execute(
            "select count(*) as cnt from jobs where run_id=%s", ("run-sb-1",)
        ).fetchone()
        nodes = conn.execute(
            "select count(*) as cnt from job_nodes where job_id=%s",
            ("ws-sb_wf_item-00000",),
        ).fetchone()
    assert str(title["title"]) == "Second Title"
    assert int(rows["cnt"]) == 2
    # The duplicate id did not double-insert its node rows either.
    assert int(nodes["cnt"]) == len(_NODE_KEYS)


@pytest.mark.fresh_schema
def test_bulk_insert_fires_statement_trigger_once_per_batch(tmp_path: Path) -> None:
    # Each jobs INSERT makes the v77 statement trigger upsert one counter row
    # per (run_id, status). A synchronous observer records those actual writes
    # in the same transaction; pg_stat_user_tables cannot provide this exact
    # count because its asynchronous, table-wide deltas admit unrelated writes.
    db = _make_db(tmp_path)
    _seed_workspace(db, "ws-sb2")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            create table bulk_counter_write_audit(
              id bigserial primary key,
              txid bigint not null,
              run_id text not null,
              status text not null,
              operation text not null
            );
            create function record_bulk_counter_write() returns trigger as $$
            begin
              insert into bulk_counter_write_audit(txid, run_id, status, operation)
              values (txid_current(), NEW.run_id, NEW.status, TG_OP);
              return null;
            end;
            $$ language plpgsql;
            create trigger bulk_counter_write_audit
              after insert or update on run_job_status_counts
              for each row execute function record_bulk_counter_write();
            """
        )

    db.create_jobs_bulk(
        candidates=[_candidate(i) for i in range(_ROW_COUNT)],
        workflow_key="wf",
        run_id="run-sb-1",
        node_keys=["node_a"],
        workspace_id="ws-sb2",
        revision=_REVISION,
    )

    expected = -(-_ROW_COUNT // CREATE_CHUNK_ROWS)
    with read_connection(TEST_DATABASE_URL) as conn:
        observed = conn.execute(
            "select count(*) as writes, count(distinct txid) as transactions,"
            " array_agg(operation order by id) as operations"
            " from bulk_counter_write_audit where run_id=%s and status='queued'",
            ("run-sb-1",),
        ).fetchone()
        truth = conn.execute(
            "select status, count(*) from jobs where run_id=%s group by status",
            ("run-sb-1",),
        ).fetchall()
        run_counts = conn.execute(
            "select status, cnt from run_job_status_counts where run_id=%s",
            ("run-sb-1",),
        ).fetchall()

    # The first chunk creates the counter and later chunks update it. Exactly
    # one write in each distinct chunk transaction rules out both per-row
    # trigger economics and accidentally coalescing the per-chunk commits.
    assert int(observed["writes"]) == expected, dict(observed)
    assert int(observed["transactions"]) == expected, dict(observed)
    assert list(observed["operations"]) == ["INSERT"] + ["UPDATE"] * (expected - 1)
    assert _counter_map(run_counts) == _counter_map(truth) == {"queued": _ROW_COUNT}
