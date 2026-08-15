"""Quality stats: grouped aggregates and the review confusion matrix (schema v28).

Positive class = 拦截 (review rejected: run_status='failed' and
failure_detail='review_rejected'); completed runs count as 放行. Labeled
items with any other outcome (e.g. infrastructure failures) are excluded
from the matrix, which is computed for every group — only review_* nodes
give it a meaningful reading.
"""

from __future__ import annotations

import itertools

import pytest

from server.app.db.transaction import write_transaction
from server.app.services.job_errors import NotFoundError
from server.app.services.quality_labels import QualityLabelService
from server.app.services.quality_stats import QualityStatsService
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.fresh_schema

WORKSPACE = "ws-quality"
_RUN_IDS = itertools.count(1)


def _stats() -> QualityStatsService:
    return QualityStatsService(TEST_DATABASE_URL)


def _labels() -> QualityLabelService:
    return QualityLabelService(TEST_DATABASE_URL)


def _seed_item(
    conn,
    item_id: str,
    *,
    node_key: str = "review_key_info",
    run_status: str = "completed",
    failure_detail: str = "",
    skill_version: str = "v1",
    provider: str = "gateway",
    model: str = "model-x",
) -> None:
    conn.execute(
        "insert into quality_sample_items("
        "id, batch_id, node_run_id, job_id, node_key, skill_version, provider, model,"
        " run_status, failure_detail)"
        " values (%s, 'batch-1', %s, 'job-1', %s, %s, %s, %s, %s, %s)",
        (
            item_id,
            next(_RUN_IDS),
            node_key,
            skill_version,
            provider,
            model,
            run_status,
            failure_detail,
        ),
    )


def _seed_batch(conn) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'question_comprehension_info') on conflict do nothing",
        (WORKSPACE, WORKSPACE),
    )
    conn.execute(
        "insert into quality_sample_batches(id, workspace_id, name, sample_size, seed)"
        " values ('batch-1', %s, 'batch', 10, 'seed')",
        (WORKSPACE,),
    )


def _seed_review_group(conn) -> None:
    _seed_item(conn, "item-tn", run_status="completed")
    _seed_item(conn, "item-fn", run_status="completed")
    _seed_item(conn, "item-fp", run_status="failed", failure_detail="review_rejected")
    _seed_item(conn, "item-tp", run_status="failed", failure_detail="review_rejected")
    # Infrastructure failure and unlabeled items never enter the matrix.
    _seed_item(conn, "item-infra", run_status="failed", failure_detail="worker timeout")
    _seed_item(conn, "item-unlabeled", run_status="completed")


def _group(groups: list[dict], node_key: str) -> dict:
    matches = [group for group in groups if group["node_key"] == node_key]
    assert len(matches) == 1
    return matches[0]


def test_confusion_matrix_counts_and_rates():
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_batch(conn)
        _seed_review_group(conn)
    labels = _labels()
    labels.add_label(WORKSPACE, "item-tn", verdict="good")
    labels.add_label(WORKSPACE, "item-fn", verdict="bad")
    labels.add_label(WORKSPACE, "item-fp", verdict="good")
    labels.add_label(WORKSPACE, "item-tp", verdict="bad")
    labels.add_label(WORKSPACE, "item-infra", verdict="bad")

    group = _group(_stats().batch_stats(WORKSPACE, "batch-1"), "review_key_info")
    assert group["runs"] == 6
    assert group["labeled"] == 5
    matrix = group["confusion_matrix"]
    assert matrix is not None
    assert (matrix["tp"], matrix["fp"], matrix["fn"], matrix["tn"]) == (1, 1, 1, 1)
    assert matrix["precision"] == pytest.approx(0.5)
    assert matrix["recall"] == pytest.approx(0.5)
    assert matrix["accuracy"] == pytest.approx(0.5)


def test_group_without_classifiable_labeled_items_has_null_matrix():
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_batch(conn)
        _seed_item(conn, "item-1", node_key="generate_key_info")
    group = _group(_stats().batch_stats(WORKSPACE, "batch-1"), "generate_key_info")
    assert group["runs"] == 1
    assert group["labeled"] == 0
    assert group["confusion_matrix"] is None


def test_matrix_computed_for_non_review_groups_too():
    """Backend does not hardcode review capabilities: a labeled, classifiable
    item yields a matrix on any node_key; presentation filters to review_*."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_batch(conn)
        _seed_item(conn, "item-1", node_key="generate_key_info", run_status="completed")
    _labels().add_label(WORKSPACE, "item-1", verdict="good")
    group = _group(_stats().batch_stats(WORKSPACE, "batch-1"), "generate_key_info")
    matrix = group["confusion_matrix"]
    assert matrix is not None
    assert (matrix["tp"], matrix["fp"], matrix["fn"], matrix["tn"]) == (0, 0, 0, 1)
    assert matrix["precision"] is None
    assert matrix["recall"] is None
    assert matrix["accuracy"] == pytest.approx(1.0)


def test_latest_label_wins_in_matrix():
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_batch(conn)
        _seed_item(conn, "item-1", run_status="failed", failure_detail="review_rejected")
    labels = _labels()
    stale = labels.add_label(WORKSPACE, "item-1", verdict="good")
    labels.add_label(WORKSPACE, "item-1", verdict="bad")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update quality_labels set created_at = current_timestamp - interval '1 hour'"
            " where id = %s",
            (stale["id"],),
        )
    group = _group(_stats().batch_stats(WORKSPACE, "batch-1"), "review_key_info")
    matrix = group["confusion_matrix"]
    assert matrix is not None
    assert (matrix["tp"], matrix["fp"], matrix["fn"], matrix["tn"]) == (1, 0, 0, 0)
    assert matrix["precision"] == pytest.approx(1.0)
    assert matrix["recall"] == pytest.approx(1.0)


def test_unknown_batch_raises_not_found():
    with pytest.raises(NotFoundError):
        _stats().batch_stats(WORKSPACE, "missing")
