"""Tests for scripts.backfill_comprehension_jobdir_ids against a real DB."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import backfill_comprehension_jobdir_ids as bjd
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.artifact_store import ArtifactStore
from tests.postgres_support import TEST_DATABASE_URL

SINCE = datetime(2000, 1, 1, tzinfo=UTC)  # every test blob is newer than this
OLD_PE = "pe_11111111-1111-1111-1111-111111111111"  # uuid shape, wrong version/variant
NEW_PE = "pe_92436b6d-4614-40f0-9e11-11100e417ec4"
OLD_KI = "ki_460bd7aa"  # truncated token in report prose
NEW_KI = "ki_460bd7aa-6c83-40e2-a03d-f3c73490eeb9"
GOOD_PE = "pe_a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"


@pytest.fixture
def env(tmp_path):
    init_db(TEST_DATABASE_URL)
    store = ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
    return store, tmp_path / "data"


def _make_job(data_dir: Path, job_id: str, *, packed: int = 0) -> Path:
    storage_dir = f"jobs/wf/ab/{job_id}"
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws', 'ws', 'question_comprehension_info') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir, packed)"
            " values (%s, 'ws', 'wf', 's', %s, 't', 'pending', %s, %s)",
            (job_id, job_id, storage_dir, packed),
        )
    job_dir = data_dir / storage_dir
    job_dir.mkdir(parents=True)
    return job_dir


def _rewrite_ref(store: ArtifactStore, job_id: str, name: str, new_content: str) -> None:
    """Simulate the artifact backfill: point the ref at a freshly created blob."""
    store.add_ref(job_id, "node1", name, store.put(new_content.encode("utf-8")))


def _packed(job_id: str) -> int:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select packed from jobs where id = %s", (job_id,)).fetchone()
    return int(row["packed"])


def _mark_active(job_id: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into agent_execution_requests(execution_id, workspace_id, job_id,"
            " workflow_key, node_key, agent_id, agent_definition_hash,"
            " node_concurrency_limit, state, queued_at, manifest_json)"
            " values (%s, 'ws', %s, 'wf', 'n1', 'agent', 'hash', 1, 'queued', now(), '{}')",
            (f"exec-{job_id}", job_id),
        )


def _pe_doc(error_id: str) -> str:
    return json.dumps({"possible_error_list": [{"error_id": error_id}]})


def _sync(env, *, apply: bool) -> bjd.SyncStats:
    store, data_dir = env
    return bjd.sync_job_dirs(TEST_DATABASE_URL, store.root, data_dir, SINCE, apply=apply)


def test_positional_mapping_overwrites_and_token_syncs(env):
    store, data_dir = env
    job_dir = _make_job(data_dir, "j1", packed=1)
    (job_dir / "possible_errors_raw.json").write_text(_pe_doc(OLD_PE))
    (job_dir / "comprehension_info.json").write_text(
        json.dumps({"errors": [{"id": OLD_PE}, {"id": GOOD_PE}]})
    )
    _rewrite_ref(store, "j1", "possible_errors_raw.json", _pe_doc(NEW_PE))

    stats = _sync(env, apply=True)

    assert stats.affected_jobs == 1 and stats.mapping_entries == 1
    assert stats.files_overwritten == 1 and stats.files_token_synced == 1
    assert stats.packed_reset == 1
    assert (job_dir / "possible_errors_raw.json").read_text() == _pe_doc(NEW_PE)
    info = json.loads((job_dir / "comprehension_info.json").read_text())
    assert [e["id"] for e in info["errors"]] == [NEW_PE, GOOD_PE]
    assert _packed("j1") == 0
    bad, _ = bjd.verify(TEST_DATABASE_URL, data_dir, SINCE)
    assert bad == 0


def test_single_diff_pairing_when_lengths_differ(env):
    store, data_dir = env
    job_dir = _make_job(data_dir, "j1")
    old_report = f"approved {GOOD_PE}; rejected {OLD_KI}"
    (job_dir / "key_info_review_report.json").write_text(old_report)
    # New blob gained an extra trailing token, so positional zip cannot work.
    new_report = f"approved {GOOD_PE}; rejected {NEW_KI}; see {NEW_KI}"
    _rewrite_ref(store, "j1", "key_info_review_report.json", new_report)
    (job_dir / "comprehension_info.json").write_text(f"ref {OLD_KI} here")

    stats = _sync(env, apply=True)

    assert stats.mapping_entries == 1 and stats.skipped_jobs == 0
    assert (job_dir / "key_info_review_report.json").read_text() == new_report
    assert (job_dir / "comprehension_info.json").read_text() == f"ref {NEW_KI} here"


def test_conflicting_mapping_skips_job(env):
    store, data_dir = env
    job_dir = _make_job(data_dir, "j1", packed=1)
    (job_dir / "possible_errors_raw.json").write_text(_pe_doc(OLD_PE))
    (job_dir / "key_info_review_report.json").write_text(f"token {OLD_PE}")
    _rewrite_ref(store, "j1", "possible_errors_raw.json", _pe_doc(NEW_PE))
    # Same old token paired with a *different* new token in another file.
    _rewrite_ref(store, "j1", "key_info_review_report.json", f"token {NEW_KI}")
    info_path = job_dir / "comprehension_info.json"
    info_path.write_text(f"ref {OLD_PE}")

    stats = _sync(env, apply=True)

    assert stats.skipped_jobs == 1 and stats.files_overwritten == 0
    assert info_path.read_text() == f"ref {OLD_PE}"
    assert _packed("j1") == 1  # not reset: job was not processed


def test_active_job_is_skipped(env):
    store, data_dir = env
    job_dir = _make_job(data_dir, "j1")
    (job_dir / "possible_errors_raw.json").write_text(_pe_doc(OLD_PE))
    _rewrite_ref(store, "j1", "possible_errors_raw.json", _pe_doc(NEW_PE))
    _mark_active("j1")

    stats = _sync(env, apply=True)

    assert stats.skipped_active_jobs == 1 and stats.files_overwritten == 0
    assert (job_dir / "possible_errors_raw.json").read_text() == _pe_doc(OLD_PE)
    bad, in_active = bjd.verify(TEST_DATABASE_URL, data_dir, SINCE)
    assert bad == 1 and in_active == 1


def test_rerun_is_idempotent_and_leftover_counted(env):
    store, data_dir = env
    job_dir = _make_job(data_dir, "j1", packed=1)
    (job_dir / "possible_errors_raw.json").write_text(_pe_doc(OLD_PE))
    # A bad token with no mapping anywhere: reported as leftover, untouched.
    (job_dir / "questions.json").write_text(f'{{"note": "{OLD_KI}"}}')
    _rewrite_ref(store, "j1", "possible_errors_raw.json", _pe_doc(NEW_PE))

    first = _sync(env, apply=True)
    assert first.files_overwritten == 1 and first.leftover_tokens == 1
    second = _sync(env, apply=True)
    assert second.files_overwritten == 0 and second.files_token_synced == 0
    assert second.packed_reset == 0
    bad, _ = bjd.verify(TEST_DATABASE_URL, data_dir, SINCE)
    assert bad == 1


def test_dry_run_reports_without_writing(env):
    store, data_dir = env
    job_dir = _make_job(data_dir, "j1", packed=1)
    (job_dir / "possible_errors_raw.json").write_text(_pe_doc(OLD_PE))
    _rewrite_ref(store, "j1", "possible_errors_raw.json", _pe_doc(NEW_PE))

    stats = _sync(env, apply=False)

    assert stats.files_overwritten == 1 and stats.packed_reset == 1
    assert (job_dir / "possible_errors_raw.json").read_text() == _pe_doc(OLD_PE)
    assert _packed("j1") == 1
