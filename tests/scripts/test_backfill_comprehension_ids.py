"""Tests for scripts.backfill_comprehension_ids against a real DB and store."""

from __future__ import annotations

import json
import re

import pytest

from scripts import backfill_comprehension_ids as bci
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.artifact_store import ArtifactStore
from tests.postgres_support import TEST_DATABASE_URL

GOOD_PE = "pe_" + "a" * 8 + "-aaaa-4aaa-8aaa-" + "a" * 12
GOOD_KI = "ki_" + "b" * 8 + "-bbbb-4bbb-8bbb-" + "b" * 12
BAD_PE = "pe_11111111-1111-1111-1111-111111111111"  # uuid shape, wrong version/variant
BAD_KI = "ki_placeholder"
STRICT = re.compile(
    r"^(pe|ki)_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@pytest.fixture
def store(tmp_path):
    init_db(TEST_DATABASE_URL)
    return ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)


def _make_job(job_id: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws', 'ws', 'question_comprehension_info') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir) values (%s, 'ws', 'wf', 's', %s, 't', 'pending', 'd')",
            (job_id, job_id),
        )


def _mark_active(job_id: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into agent_execution_requests(execution_id, workspace_id, job_id,"
            " workflow_key, node_key, agent_id, agent_definition_hash,"
            " node_concurrency_limit, state, queued_at, manifest_json)"
            " values (%s, 'ws', %s, 'wf', 'n1', 'agent', 'hash', 1, 'queued', now(), '{}')",
            (f"exec-{job_id}", job_id),
        )


def _put(store: ArtifactStore, job_id: str, name: str, payload) -> None:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    store.add_ref(job_id, "node1", name, store.put(data.encode("utf-8")))


def _get(store: ArtifactStore, job_id: str, name: str) -> str:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select hash from artifact_refs where job_id = %s and name = %s",
            (job_id, name),
        ).fetchone()
    return store.open(row["hash"]).read_bytes().decode("utf-8")


def _pe_doc(error_id: str, related: list[str] | None = None) -> dict:
    return {
        "possible_error_list": [
            {"error_id": error_id, "related_key_info_ids": related or []},
        ]
    }


def _ki_doc(key_info_id: str) -> dict:
    return {"key_info_list": [{"key_info_id": key_info_id, "content": "c"}]}


def test_replaces_fake_pe_id_consistently_across_names(store):
    _make_job("j1")
    _put(store, "j1", "possible_errors_raw.json", _pe_doc(BAD_PE, [GOOD_KI]))
    _put(store, "j1", "possible_errors_reviewed.json", _pe_doc(BAD_PE, [GOOD_KI]))
    _put(store, "j1", "possible_errors_review_report.json", f"Review rejected: {BAD_PE}")

    stats = bci.backfill_comprehension_ids(TEST_DATABASE_URL, store.root, apply=True)

    assert stats.bad_pe == 1 and stats.bad_ki == 0
    assert stats.affected_jobs == 1 and stats.rewritten_artifacts == 3
    new_id = json.loads(_get(store, "j1", "possible_errors_raw.json"))["possible_error_list"][0][
        "error_id"
    ]
    assert STRICT.match(new_id) and new_id != BAD_PE
    reviewed = json.loads(_get(store, "j1", "possible_errors_reviewed.json"))
    assert reviewed["possible_error_list"][0]["error_id"] == new_id
    report = _get(store, "j1", "possible_errors_review_report.json")
    assert new_id in report and BAD_PE not in report
    # Compliant ids and cross-references are untouched.
    assert reviewed["possible_error_list"][0]["related_key_info_ids"] == [GOOD_KI]


def test_collision_maps_to_different_new_ids_per_job(store):
    shared = "pe_" + "c" * 8 + "-cccc-4ccc-8ccc-" + "c" * 12  # compliant shape but shared
    for job_id in ("j1", "j2"):
        _make_job(job_id)
        _put(store, job_id, "possible_errors_raw.json", _pe_doc(shared))

    stats = bci.backfill_comprehension_ids(TEST_DATABASE_URL, store.root, apply=True)

    assert stats.bad_pe == 1 and stats.affected_jobs == 2
    new_ids = {
        json.loads(_get(store, job_id, "possible_errors_raw.json"))["possible_error_list"][0][
            "error_id"
        ]
        for job_id in ("j1", "j2")
    }
    assert len(new_ids) == 2 and shared not in new_ids
    assert all(STRICT.match(i) for i in new_ids)


def test_related_key_info_ids_follow_regenerated_ki_id(store):
    _make_job("j1")
    _put(store, "j1", "key_info_raw.json", _ki_doc(BAD_KI))
    _put(store, "j1", "possible_errors_raw.json", _pe_doc(GOOD_PE, [BAD_KI]))

    stats = bci.backfill_comprehension_ids(TEST_DATABASE_URL, store.root, apply=True)

    assert stats.bad_ki == 1 and stats.bad_pe == 0
    new_ki = json.loads(_get(store, "j1", "key_info_raw.json"))["key_info_list"][0]["key_info_id"]
    assert STRICT.match(new_ki) and new_ki.startswith("ki_")
    pe_doc = json.loads(_get(store, "j1", "possible_errors_raw.json"))
    assert pe_doc["possible_error_list"][0]["related_key_info_ids"] == [new_ki]
    assert pe_doc["possible_error_list"][0]["error_id"] == GOOD_PE


def test_active_job_is_skipped(store):
    _make_job("j1")
    _mark_active("j1")
    _put(store, "j1", "possible_errors_raw.json", _pe_doc(BAD_PE))

    stats = bci.backfill_comprehension_ids(TEST_DATABASE_URL, store.root, apply=True)

    assert stats.skipped_active_jobs == 1 and stats.rewritten_artifacts == 0
    doc = json.loads(_get(store, "j1", "possible_errors_raw.json"))
    assert doc["possible_error_list"][0]["error_id"] == BAD_PE


def test_rerun_is_idempotent(store):
    _make_job("j1")
    _put(store, "j1", "possible_errors_raw.json", _pe_doc(BAD_PE))
    _put(store, "j1", "key_info_reviewed_lean.json", _ki_doc(BAD_KI))

    first = bci.backfill_comprehension_ids(TEST_DATABASE_URL, store.root, apply=True)
    assert first.rewritten_artifacts == 2
    second = bci.backfill_comprehension_ids(TEST_DATABASE_URL, store.root, apply=True)
    assert second.bad_pe == 0 and second.bad_ki == 0
    assert second.affected_jobs == 0 and second.rewritten_artifacts == 0
    residual, _ = bci.verify(TEST_DATABASE_URL, store.root)
    assert residual == 0


def test_dry_run_reports_without_writing(store):
    _make_job("j1")
    _put(store, "j1", "possible_errors_raw.json", _pe_doc(BAD_PE))

    stats = bci.backfill_comprehension_ids(TEST_DATABASE_URL, store.root, apply=False)

    assert stats.rewritten_artifacts == 1
    doc = json.loads(_get(store, "j1", "possible_errors_raw.json"))
    assert doc["possible_error_list"][0]["error_id"] == BAD_PE


def test_unprefixed_literal_replaced_only_as_json_string(store):
    _make_job("j1")
    _put(store, "j1", "key_info_raw.json", _ki_doc("placeholder"))
    report = 'See placeholder in prose; JSON value "placeholder" was rejected.'
    _put(store, "j1", "key_info_review_report.json", report)

    bci.backfill_comprehension_ids(TEST_DATABASE_URL, store.root, apply=True)

    new_ki = json.loads(_get(store, "j1", "key_info_raw.json"))["key_info_list"][0]["key_info_id"]
    assert STRICT.match(new_ki)
    new_report = _get(store, "j1", "key_info_review_report.json")
    assert "See placeholder in prose" in new_report  # bare word untouched
    assert f'"{new_ki}"' in new_report


def test_truncated_id_maps_to_unique_full_form_in_same_job(store):
    """A truncated reference in report prose re-points to the full id."""
    full = "ki_460bd7aa-6c83-40e2-a03d-f3c73490eeb9"
    _make_job("j1")
    _put(store, "j1", "key_info_raw.json", _ki_doc(full))
    _put(store, "j1", "key_info_review_report.json", "关联的 ki_460bd7aa 指向关键词信息")

    bci.backfill_comprehension_ids(TEST_DATABASE_URL, store.root, apply=True)

    report = _get(store, "j1", "key_info_review_report.json")
    assert f"关联的 {full} 指向" in report
    # The structured file already carried the compliant full id: untouched.
    doc = json.loads(_get(store, "j1", "key_info_raw.json"))
    assert doc["key_info_list"][0]["key_info_id"] == full
    residual, _ = bci.verify(TEST_DATABASE_URL, store.root)
    assert residual == 0
