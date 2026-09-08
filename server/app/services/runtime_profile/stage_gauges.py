"""Stage-gauge families for the runtime profile (#448 claim, #521 result).

Both families share the (total, max) float-pair shape: for each stage the
``claim_timing`` / ``result_timing`` modules report per-attempt seconds,
and this mixin folds them into per-bucket gauges — reset/snapshot loop
over the tuples, the note_* folds share ``_fold_stages``. Kept as one
mixin (composed onto ``RuntimeProfile`` in ``counters.py``) so the single
undercount discipline the registry documents stays in one place: a plain
``+=`` under the GIL may lose a concurrent increment, a bounded undercount
acceptable for triage gauges.

COUNTING discipline: neither fold touches the family-wide counters
(claim_count / result_count / *_seconds_total) — those belong to
``note_claim`` / ``note_result`` on the broker/route lifecycles; an early
claim-side variant double-counted ``claim_empty_count`` and doubled the
classifier's empty_claim_ratio (#461 review).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.services.runtime_profile.counters import RuntimeProfileCounters

# Claim-stage gauges (#448): set from ``claim_timing`` stage names.
CLAIM_STAGES = ("scan", "evaluate", "writes")

# Result-stage gauges (#521): set from ``result_timing`` stage names.
RESULT_STAGES = (
    "unpack",
    "artifacts_verify",
    "validate",
    "artifacts_upload",
    "lease_write",
    "events",
    "mark_done",
)

# Both stage families share the (total, max) gauge shape: reset/snapshot
# zero/copy them through one loop each over (prefix, stages) pairs.
STAGE_FAMILIES = (("claim", CLAIM_STAGES), ("result", RESULT_STAGES))


class StageGaugesMixin:
    """note_*_stages folds + the shared accumulation body.

    Composed onto ``RuntimeProfile``, which owns ``counters``; typed as a
    property here so mypy sees the host's contract (the attribute itself
    only exists on the composed class).
    """

    counters: RuntimeProfileCounters

    def note_claim_stages(self, stages: Mapping[str, float]) -> None:
        """Fold one claim's per-stage timings into the claim gauges (#448).

        Unknown keys are ignored (worker_setup/commit fold into nothing —
        the claim-wide totals already carry them).
        """
        self._fold_stages("claim", CLAIM_STAGES, stages)

    def note_result_stages(self, stages: Mapping[str, float]) -> None:
        """Fold one result commit's per-stage timings into the gauges (#521).

        Unknown keys are ignored, same as the claim fold.
        """
        self._fold_stages("result", RESULT_STAGES, stages)

    def _fold_stages(self, prefix: str, known: tuple[str, ...], stages: Mapping[str, float]) -> None:  # fmt: skip
        """Accumulate (total, max) per stage; unknown keys fold into nothing."""
        for stage in known:
            seconds = stages.get(stage, 0.0)
            if not seconds:
                continue
            setattr(
                self.counters,
                f"{prefix}_{stage}_seconds_total",
                getattr(self.counters, f"{prefix}_{stage}_seconds_total") + seconds,
            )
            setattr(
                self.counters,
                f"{prefix}_{stage}_seconds_max",
                max(getattr(self.counters, f"{prefix}_{stage}_seconds_max"), seconds),
            )
