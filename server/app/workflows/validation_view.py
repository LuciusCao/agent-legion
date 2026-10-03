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

- inputs are private copies (reflink/CoW first via ``_reflink_copy``, full
  copy as the fallback) — construction-time write isolation: a validator's
  in-place write or chmod on an input can never cross into the job dir's
  upstream artifact through a shared inode, and CoW filesystems keep the
  zero-copy cost discipline (full input copies on every completion would
  scale the completion path's time and scratch usage with the input size);
- an input's BYTES come from the dispatch-frozen CAS copy when the manifest
  carries an ``input_artifacts`` ref for it (#828/#830/#833:
  ``stage_agent_inputs`` freezes what the Worker actually consumed at
  dispatch; a parallel producer may overwrite the job-dir file before
  completion) — no ref / non-CAS ref shape / missing blob falls back to the
  job dir. The isolation above still applies to CAS-sourced bytes: the
  private copy keeps a validator's writes out of the shared blob, so the
  two defenses stack (CAS reads the right bytes, the private inode keeps
  them write-isolated);
- a name declared as both input and output resolves to the output bytes
  and is exempt from the input read-only check — compared on the
  normalized spelling (``safe_relative`` collapses ``./``/``//``), so a
  non-canonical input declaration cannot smuggle stale job-dir bytes over
  this attempt's fresh output (#833 codex P2, the #779 final-review P1
  residue exclusion restated for the view);
- outputs stay hardlinked (copy fallback on hardlink-unsupported mounts) so
  the validator's in-place cleaning keeps propagating to the bytes the
  finish gate promotes; the replace family is covered by the reconcile arm;
- a missing declared source is simply absent from the view (fail-open) —
  the validator's own rules judge absence; a placement FAILURE (disk full,
  permissions, unrepresentable prefix collision) raises, so an unbuildable
  view fails closed through the Validator error channel instead of showing
  the validator a silently incomplete view;
- inputs are read-only by contract: the exit check still snapshots each
  input's (inode, mtime, size) and fails closed on any change (in-place
  write, replace, delete) — now defense in depth behind the construction-
  time isolation, never the only barrier;
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
from server.app.workflows._validation_view_inputs import (
    InputAuthority,
    resolve_input_source,
)


@contextmanager
def validation_view(
    job_dir: Path,
    *,
    inputs: Iterable[str],
    outputs: Iterable[str],
    output_source: Path,
    input_authority: InputAuthority | None = None,
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
        placements = materialize_validation_view(
            view_path, inputs, outputs, job_dir, output_source, input_authority
        )
        yield view_path
        reconcile_outputs(view_path, placements.outputs, output_source)
        verify_inputs_untouched(view_path, placements.inputs)


def materialize_validation_view(
    target: Path,
    inputs: Iterable[str],
    outputs: Iterable[str],
    input_source: Path,
    output_source: Path,
    input_authority: InputAuthority | None = None,
) -> ViewPlacements:
    """Place the declared names into ``target`` — inputs first, outputs win.

    Returns the placement record the exit arms (reconcile / read-only
    enforcement) work against. Names declared as both input and output are
    placed from ``output_source`` and exempt from the input snapshot. Other
    inputs resolve their bytes through ``input_authority`` (dispatch-frozen
    CAS first, job-dir fallback — see ``resolve_input_source``). Duplicate
    declarations are deduped on the normalized name (#868): a second
    placement would delete the first private copy and leave its snapshot
    pointing at a dead inode, misfiring the read-only check.
    """
    output_rels = {rel for name in outputs if (rel := safe_relative(name)) is not None}
    input_snaps: list[InputSnapshot] = []
    seen: set[str] = set()
    for name in inputs:
        rel = safe_relative(name)
        if rel is None or rel in output_rels or rel in seen:
            continue
        seen.add(rel)
        source = resolve_input_source(name, rel, input_authority, input_source)
        placed = place(rel, source, target, private=True)
        if placed is not None:
            st = placed[0]
            input_snaps.append(InputSnapshot(rel, st.st_ino, st.st_dev, st.st_mtime_ns, st.st_size))
    output_placements: list[OutputPlacement] = []
    for name in outputs:
        rel = safe_relative(name)
        if rel is None or rel in seen:
            continue
        seen.add(rel)
        placed = place(rel, output_source / rel, target, private=False)
        if placed is not None:
            st, linked = placed
            output_placements.append(OutputPlacement(rel, st.st_ino, st.st_dev, linked))
    return ViewPlacements(tuple(input_snaps), tuple(output_placements))
