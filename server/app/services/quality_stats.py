"""Batch-level quality aggregates over sampled items and their current labels."""

from __future__ import annotations

from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection
from server.app.services.job_errors import NotFoundError

# Review-outcome classification for the confusion matrix. A review run that
# completed approved everything (放行); a run failed with
# failure_detail='review_rejected' rejected the content (拦截). Any other
# failure is an infrastructure/execution error and is excluded from the
# matrix. The positive class is 拦截 (rejection).
_PASS = "i.run_status = 'completed'"
_REJECT = "(i.run_status = 'failed' and i.failure_detail = 'review_rejected')"


def _confusion(row: dict[str, Any]) -> dict[str, Any] | None:
    tp = int(row["tp"])
    fp = int(row["fp"])
    fn = int(row["fn"])
    tn = int(row["tn"])
    total = tp + fp + fn + tn
    if total == 0:
        return None
    intercepted = tp + fp
    actual_bad = tp + fn
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / intercepted if intercepted else None,
        "recall": tp / actual_bad if actual_bad else None,
        "accuracy": (tp + tn) / total,
    }


class QualityStatsService:
    def __init__(self, db_path: DatabaseDsn) -> None:
        self.db_path = db_path

    def batch_stats(self, workspace_id: str, batch_id: str) -> list[dict[str, Any]]:
        """Group items by (node_key, skill_version, provider, model).

        Counts runs, successes (run_status = 'completed'), and latest-wins
        'run'-target labels; rates are derived in Python so empty groups
        report None instead of dividing by zero. Each group also carries a
        confusion matrix over labeled items with a classifiable review
        outcome (completed = 放行, failed + 'review_rejected' = 拦截);
        groups without any such item report confusion_matrix = None. The
        matrix is computed for every group — only review_* nodes give it a
        meaningful reading, which is a presentation concern.
        """
        with read_connection(self.db_path) as conn:
            batch = conn.execute(
                "select id from quality_sample_batches where id = %s and workspace_id = %s",
                (batch_id, workspace_id),
            ).fetchone()
            if batch is None:
                raise NotFoundError("Sample batch not found")
            rows = conn.execute(
                f"""
                select
                  i.node_key,
                  i.skill_version,
                  i.provider,
                  i.model,
                  count(*)::int as runs,
                  count(*) filter (where i.run_status = 'completed')::int as succeeded,
                  count(lab.verdict)::int as labeled,
                  count(*) filter (where lab.verdict = 'good')::int as good,
                  count(*) filter (where lab.verdict = 'bad')::int as bad,
                  count(*) filter (where lab.verdict = 'bad' and {_REJECT})::int as tp,
                  count(*) filter (where lab.verdict = 'good' and {_REJECT})::int as fp,
                  count(*) filter (where lab.verdict = 'bad' and {_PASS})::int as fn,
                  count(*) filter (where lab.verdict = 'good' and {_PASS})::int as tn
                from quality_sample_items i
                left join lateral (
                  select l.verdict
                  from quality_labels l
                  where l.item_id = i.id and l.target = 'run'
                  order by l.created_at desc, l.id desc
                  limit 1
                ) lab on true
                where i.batch_id = %s
                group by i.node_key, i.skill_version, i.provider, i.model
                order by i.node_key, i.skill_version, i.provider, i.model
                """,
                (batch_id,),
            ).fetchall()
        groups = []
        for row in rows:
            runs = int(row["runs"])
            labeled = int(row["labeled"])
            good = int(row["good"])
            groups.append(
                {
                    "node_key": row["node_key"],
                    "skill_version": row["skill_version"],
                    "provider": row["provider"],
                    "model": row["model"],
                    "runs": runs,
                    "succeeded": int(row["succeeded"]),
                    "success_rate": int(row["succeeded"]) / runs if runs else 0.0,
                    "labeled": labeled,
                    "good": good,
                    "bad": int(row["bad"]),
                    "good_rate": good / labeled if labeled else None,
                    "confusion_matrix": _confusion(row),
                }
            )
        return groups
