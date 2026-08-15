"""Quality labels: insert-only history, latest-wins reads, artifact detail (schema v28)."""

from __future__ import annotations

import pytest

from server.app.db.transaction import read_connection, write_transaction
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.quality_labels import QualityLabelService
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.fresh_schema

WORKSPACE = "ws-quality"


def _service(artifact_store: ArtifactStore | None = None) -> QualityLabelService:
    return QualityLabelService(TEST_DATABASE_URL, artifact_store)


def _seed_item(item_id: str = "item-1", batch_id: str = "batch-1") -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'question_comprehension_info') on conflict do nothing",
            (WORKSPACE, WORKSPACE),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values ('job-1', %s, 'wf-a', 'test', 'job-1') on conflict (id) do nothing",
            (WORKSPACE,),
        )
        conn.execute(
            "insert into quality_sample_batches(id, workspace_id, name, sample_size, seed)"
            " values (%s, %s, 'batch', 10, 'seed') on conflict (id) do nothing",
            (batch_id, WORKSPACE),
        )
        conn.execute(
            "insert into quality_sample_items(id, batch_id, node_run_id, job_id, node_key)"
            " values (%s, %s, 1, 'job-1', 'node-a')",
            (item_id, batch_id),
        )


def _label_count(item_id: str) -> int:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select count(*) as cnt from quality_labels where item_id = %s", (item_id,)
        ).fetchone()
    return int(row["cnt"]) if row is not None else 0


def test_add_label_appends_without_replacing_history():
    _seed_item()
    service = _service()
    first = service.add_label(WORKSPACE, "item-1", verdict="bad", reason_codes=["fact_error"])
    assert first["verdict"] == "bad"
    assert first["target"] == "run"
    assert first["reason_codes"] == ["fact_error"]
    second = service.add_label(WORKSPACE, "item-1", verdict="good")
    assert second["verdict"] == "good"
    assert _label_count("item-1") == 2


def test_latest_label_wins_in_batch_items():
    _seed_item()
    service = _service()
    stale = service.add_label(WORKSPACE, "item-1", verdict="bad")
    service.add_label(WORKSPACE, "item-1", verdict="good")
    # Force a deterministic timestamp gap in case both inserts land in the
    # same microsecond on fast machines.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update quality_labels set created_at = current_timestamp - interval '1 hour'"
            " where id = %s",
            (stale["id"],),
        )
    page = service.list_batch_items(WORKSPACE, "batch-1")
    assert page["total"] == 1
    (item,) = page["items"]
    assert item["current_label"]["verdict"] == "good"


def test_unknown_reason_code_rejected():
    _seed_item()
    with pytest.raises(InvalidOperationError):
        _service().add_label(WORKSPACE, "item-1", verdict="bad", reason_codes=["nonsense"])
    assert _label_count("item-1") == 0


def test_list_batch_items_paginates():
    _seed_item(item_id="item-1")
    with write_transaction(TEST_DATABASE_URL) as conn:
        for index in (2, 3):
            conn.execute(
                "insert into quality_sample_items(id, batch_id, node_run_id, job_id, node_key)"
                " values (%s, 'batch-1', %s, 'job-1', 'node-a')",
                (f"item-{index}", index),
            )
    service = _service()
    page = service.list_batch_items(WORKSPACE, "batch-1", limit=2, offset=0)
    assert page["total"] == 3
    assert len(page["items"]) == 2
    rest = service.list_batch_items(WORKSPACE, "batch-1", limit=2, offset=2)
    assert len(rest["items"]) == 1


def test_item_detail_returns_label_history_and_artifacts(tmp_path):
    _seed_item()
    store = ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
    digest = store.put(b'{"answer": 42}')
    store.add_ref("job-1", "node-a", "key_info_reviewed_lean.json", digest)
    store.add_ref("job-1", "node-b", "other.json", store.put(b"{}"))
    # A dangling ref (blob missing from disk) must be skipped, not fail the
    # detail read; the catalog row exists so the FK is satisfied.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("insert into artifacts(hash, size) values (%s, 0)", ("f" * 64,))
    store.add_ref("job-1", "node-a", "missing.json", "f" * 64)

    service = _service(store)
    first = service.add_label(WORKSPACE, "item-1", verdict="bad", note="first")
    service.add_label(WORKSPACE, "item-1", verdict="good", note="second")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update quality_labels set created_at = current_timestamp - interval '1 hour'"
            " where id = %s",
            (first["id"],),
        )
    detail = service.get_item_detail(WORKSPACE, "item-1")

    assert detail["item"]["id"] == "item-1"
    assert [label["note"] for label in detail["labels"]] == ["second", "first"]
    artifact_names = {artifact["name"] for artifact in detail["artifacts"]}
    assert artifact_names == {"key_info_reviewed_lean.json"}
    assert detail["artifacts"][0]["content"] == '{"answer": 42}'
    assert detail["artifacts"][0]["truncated"] is False


def test_cross_workspace_access_raises_not_found():
    _seed_item()
    service = _service()
    with pytest.raises(NotFoundError):
        service.add_label("ws-other", "item-1", verdict="good")
    with pytest.raises(NotFoundError):
        service.get_item_detail("ws-other", "item-1")
    with pytest.raises(NotFoundError):
        service.list_batch_items("ws-other", "batch-1")
