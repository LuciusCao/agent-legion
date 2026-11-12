"""End-to-end pins for the injective job-id encoding (#1255).

The pure-function contract lives in
``tests/services/test_job_bulk_id_encoding.py``; this file pins the DB
level: the issue's cross-workspace collision example now lands as two
distinct jobs, both creation entry points (``create_job`` /
``create_jobs_bulk``) share the one encoding, pre-#1255 legacy-format rows
stay readable and keep their dedup behavior, and a legacy row whose id
coincidentally equals a NEW encoding of a different identity is still
caught by the existing identity check (structured collision error, not
silent rebinding).
"""

from __future__ import annotations

import pytest

from server.app.jobs.job_id_encoding import encode_job_id
from server.app.jobs.queries import JobQueries

_REVISION = {
    "id": "rev-inj-1",
    "version": 1,
    "definition_hash": "hash-inj",
    "definition_json": '{"nodes": {}}',
}


def _seed_workspace(db: JobQueries, workspace_id: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, %s) on conflict (id) do nothing",
            (workspace_id, workspace_id),
        )
        conn.execute(
            "insert into runs(id, workspace_id, source_kind)"
            " values (%s, %s, 'items') on conflict (id) do nothing",
            (f"run-{workspace_id}", workspace_id),
        )


def _candidate(source_id: str, entity_type: str = "question") -> dict[str, object]:
    return {
        "entity_id": source_id,
        "entity_type": entity_type,
        "title": f"Title {source_id}",
        "stem": "",
    }


def test_issue_1255_collision_example_lands_as_two_distinct_jobs(job_db: JobQueries) -> None:
    # The issue's example: workspace "a" + source "a_a_z" and workspace
    # "a_a" + source "z" both used to produce the id "a_wf_a_a_z". With the
    # escape encoding they are distinct jobs in their own workspaces.
    _seed_workspace(job_db, "a")
    _seed_workspace(job_db, "a_a")

    ids_a = job_db.create_jobs_bulk(
        candidates=[_candidate("a_a_z")],
        workflow_key="wf",
        run_id="run-a",
        node_keys=["node_a"],
        workspace_id="a",
        revision=_REVISION,
    )
    ids_a_a = job_db.create_jobs_bulk(
        candidates=[_candidate("z")],
        workflow_key="wf",
        run_id="run-a_a",
        node_keys=["node_a"],
        workspace_id="a_a",
        revision=_REVISION,
    )

    assert ids_a == [encode_job_id("a", "wf", "a_a_z")]
    assert ids_a_a == [encode_job_id("a_a", "wf", "z")]
    assert ids_a != ids_a_a
    first = job_db.get_job(ids_a[0])
    second = job_db.get_job(ids_a_a[0])
    assert first is not None and str(first["workspace_id"]) == "a"
    assert second is not None and str(second["workspace_id"]) == "a_a"
    # Storage dirs are per-job and distinct as well.
    assert str(first["storage_dir"]) != str(second["storage_dir"])


def test_create_job_and_bulk_share_the_encoding(job_db: JobQueries) -> None:
    _seed_workspace(job_db, "ws_inj")

    row = job_db.create_job(
        workflow_key="w_f",
        source_type="question",
        source_id="Q/1_a",
        run_id="run-ws_inj",
        title="t",
        node_keys=["node_a"],
        workspace_id="ws_inj",
    )
    assert str(row["id"]) == encode_job_id("ws_inj", "w_f", "Q/1_a")

    bulk_ids = job_db.create_jobs_bulk(
        candidates=[_candidate("Q/2_b")],
        workflow_key="w_f",
        run_id="run-ws_inj",
        node_keys=["node_a"],
        workspace_id="ws_inj",
        revision=_REVISION,
    )
    assert bulk_ids == [encode_job_id("ws_inj", "w_f", "Q/2_b")]


def _insert_legacy_job(db: JobQueries, workspace_id: str, legacy_id: str, source_id: str) -> None:
    """A pre-#1255 row: raw-concatenated id, inserted straight via SQL."""
    with db.connect() as conn:
        conn.execute(
            """
            insert into jobs(
              id, workspace_id, source_type, source_id, run_id, title, storage_dir, stem,
              workflow_revision_id, workflow_version, workflow_definition_hash,
              workflow_definition_snapshot_json
            )
            values (%s, %s, 'question', %s, %s, 'legacy', 'legacy/dir', '', '', 1, '', '')
            """,
            (legacy_id, workspace_id, source_id, f"run-{workspace_id}"),
        )


def test_legacy_format_job_rows_stay_readable_and_dedup(job_db: JobQueries) -> None:
    _seed_workspace(job_db, "wsleg")
    # Legacy id for source "x/y" (raw concatenation, "/" mapped to "_").
    _insert_legacy_job(job_db, "wsleg", "wsleg_wf_x_y", "x/y")

    row = job_db.get_job("wsleg_wf_x_y")
    assert row is not None
    assert str(row["source_id"]) == "x/y"
    assert job_db.get_job_workspace("wsleg_wf_x_y") == "wsleg"

    # Resubmission dedup probes (source_type, source_id) columns, never a
    # recomputed id — the legacy row still filters its source out.
    existing = job_db.filter_existing_dedup_keys(
        "wsleg", [("question", "x/y"), ("question", "new-source")]
    )
    assert existing == {("question", "x/y")}


def test_legacy_id_matching_a_new_encoding_still_raises_identity_error(
    job_db: JobQueries,
) -> None:
    # Cross-era coincidence: the legacy raw-concatenated id of source
    # "c~ud" is exactly the NEW encoding of source "c_d". The existing
    # identity check must still reject the second create instead of
    # silently rebinding the row.
    _seed_workspace(job_db, "wsx")
    assert encode_job_id("wsx", "wf", "c_d") == "wsx_wf_c~ud"
    _insert_legacy_job(job_db, "wsx", "wsx_wf_c~ud", "c~ud")

    with pytest.raises(ValueError, match="Job identity collision"):
        job_db.create_job(
            workflow_key="wf",
            source_type="question",
            source_id="c_d",
            run_id="run-wsx",
            title="t",
            node_keys=["node_a"],
            workspace_id="wsx",
        )

    row = job_db.get_job("wsx_wf_c~ud")
    assert row is not None and str(row["source_id"]) == "c~ud"
