"""Declared validation view for the Host-side legacy validator (#757).

A job dir accumulates every node's outputs across all attempts, so running a
skill's legacy ``validate_output.py`` against it lets glob-based validators
see sibling nodes' files — a sibling's stale fail verdict then flips this
node's clean run (the review A/B cross-attribution bug). The bare staging
read view overcorrects the other way: it hides the declared inputs that
cross-file validators legitimately read. The validator therefore runs
against a view constructed from the node's own declarations — its inputs
plus this attempt's declared outputs — and nothing else. The result-validate
pool task (``agent_broker/result_validate_pool``) is the single construction
site; the per-file mechanics (placement, reconcile, read-only enforcement)
live in ``_validation_view_files`` (file-size budget split). There is no
"nothing to validate" skip: the dispatch contract trio
(``workflows.skills.REQUIRED_CONTRACT_FILES``) guarantees every skill
reaching validation ships ``scripts/validate_output.py`` (a tree missing it
is the #638 poisoned-cache case and must fail closed, never skip), so the
cost discipline is zero-copy placement, not skipping.
The semantics below are pinned by
tests/workflows/test_output_validation_view.py:

- both sides are hardlinked (zero-copy; copy fallback on hardlink-
  unsupported mounts) — full input copies on every completion would scale
  the completion path's time and scratch usage with the input size;
- a missing declared source is simply absent from the view (fail-open) —
  the validator's own rules judge absence; a placement FAILURE (disk full,
  permissions, unrepresentable prefix collision) raises, so an unbuildable
  view fails closed through the Validator error channel instead of showing
  the validator a silently incomplete view;
- inputs are read-only by contract: placement snapshots each input's
  (inode, mtime, size) and the exit check fails closed on any change
  (in-place write, replace, delete) — hardlink sharing must never let a
  validator silently mutate an upstream node's artifact in the job dir;
- outputs reconcile back to the run view on clean exit: in-place writes
  already propagate through the hardlink, and the replace family (temp +
  ``os.replace``, delete-and-rewrite — the clean-in-place helpers beyond
  ``write_text``) is synced back with a temp-write + atomic replace, so a
  passing validator's cleaned bytes are what the finish gate promotes; a
  validator-DELETED output propagates the deletion (pre-view semantics:
  the finish gate's missing-source containment fails the run);
- validator-created undeclared files never propagate — the promotion plan
  is frozen at unpack time (#759), so the view is not a backdoor around
  the declared artifact surface;
- a name declared as both input and output resolves to the output bytes
  and is exempt from the input read-only check;
- remote-channel (Worker-direct S3) outputs are the pre-existing exception:
  their authority object is the Worker's own upload and the mirror skips
  them, so validator mutations reach only the local copies on every design.

A pool worker dying hard mid-construction leaks its ``.validation-view-*``
scratch dir into the job dir (same leak class as #759's staging dirs);
reclaim rides the job dir GC.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from server.app.workflows._validation_view_files import (
    InputSnapshot,
    OutputPlacement,
    ViewPlacements,
    place,
    reconcile_outputs,
    safe_relative,
    verify_inputs_untouched,
)


@contextmanager
def validation_view(
    job_dir: Path,
    *,
    inputs: Iterable[str],
    outputs: Iterable[str],
    output_source: Path,
) -> Iterator[Path]:
    """Yield a scratch dir holding exactly the declared inputs + outputs.

    The scratch dir lives inside ``job_dir`` so both sources share its
    filesystem and hardlinks work; a missing job dir falls back to the
    default temp location (everything is then simply absent). On clean body
    exit the validator's output mutations reconcile back into
    ``output_source`` and the inputs are verified untouched (a violation
    raises, failing the run closed); a body exception skips both arms.
    """
    with tempfile.TemporaryDirectory(
        prefix=".validation-view-", dir=job_dir if job_dir.is_dir() else None
    ) as view:
        view_path = Path(view)
        placements = materialize_validation_view(view_path, inputs, outputs, job_dir, output_source)
        yield view_path
        reconcile_outputs(view_path, placements.outputs, output_source)
        verify_inputs_untouched(view_path, placements.inputs)


def materialize_validation_view(
    target: Path,
    inputs: Iterable[str],
    outputs: Iterable[str],
    input_source: Path,
    output_source: Path,
) -> ViewPlacements:
    """Place the declared names into ``target`` — inputs first, outputs win.

    Returns the placement record the exit arms (reconcile / read-only
    enforcement) work against. Names declared as both input and output are
    placed from ``output_source`` and exempt from the input snapshot.
    """
    output_rels = {rel for name in outputs if (rel := safe_relative(name)) is not None}
    input_snaps: list[InputSnapshot] = []
    for name in inputs:
        rel = safe_relative(name)
        if rel is None or rel in output_rels:
            continue
        placed = place(rel, input_source, target)
        if placed is not None:
            st = placed[0]
            input_snaps.append(InputSnapshot(rel, st.st_ino, st.st_dev, st.st_mtime_ns, st.st_size))
    output_placements: list[OutputPlacement] = []
    for name in outputs:
        rel = safe_relative(name)
        if rel is None:
            continue
        placed = place(rel, output_source, target)
        if placed is not None:
            st, linked = placed
            output_placements.append(OutputPlacement(rel, st.st_ino, st.st_dev, linked))
    return ViewPlacements(tuple(input_snaps), tuple(output_placements))
