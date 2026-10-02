"""Declared validation view for the Host-side legacy validator (#757).

A job dir accumulates every node's outputs across all attempts, so running a
skill's legacy ``validate_output.py`` against it lets glob-based validators
see sibling nodes' files — a sibling's stale fail verdict then flips this
node's clean run (the review A/B cross-attribution bug). The bare staging
read view overcorrects the other way: it hides the declared inputs that
cross-file validators legitimately read. The validator therefore runs
against a view constructed from the node's own declarations — its inputs
(copied out of the job dir) plus this attempt's declared outputs (linked
from the run's read view) — and nothing else.
``worker_output_validation.validate_worker_outputs`` is the single
construction site shared by every Host-side validation entry; the semantics
below are pinned by tests/workflows/test_output_validation_view.py:

- outputs come only from this attempt's read view — stale same-name
  residues in the job dir never enter the view;
- inputs are copied, never linked, so a validator writing an input file
  cannot mutate an upstream node's artifact in the job dir;
- outputs are hardlinked (copy fallback) so a validator cleaning an output
  in place keeps reaching the to-be-promoted bytes, exactly as when the
  validator ran inside the staging read view;
- a name declared as both input and output resolves to the output bytes;
- missing declared files are simply absent — construction never fails on
  them; the validator's own rules decide whether absence is an error;
- undeclared names and unsafe (absolute / ``..``) names never enter.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath


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
    filesystem and output hardlinks work; a missing job dir falls back to
    the default temp location (inputs are then simply absent).
    """
    with tempfile.TemporaryDirectory(
        prefix=".validation-view-", dir=job_dir if job_dir.is_dir() else None
    ) as view:
        materialize_validation_view(
            Path(view),
            inputs=inputs,
            outputs=outputs,
            input_source=job_dir,
            output_source=output_source,
        )
        yield Path(view)


def materialize_validation_view(
    target: Path,
    *,
    inputs: Iterable[str],
    outputs: Iterable[str],
    input_source: Path,
    output_source: Path,
) -> None:
    """Place the declared names into ``target`` — inputs first, outputs win."""
    for name in inputs:
        _place(name, input_source, target, link=False)
    for name in outputs:
        _place(name, output_source, target, link=True)


def _place(name: str, source_dir: Path, target: Path, *, link: bool) -> None:
    rel = PurePosixPath(name)
    if rel.is_absolute() or ".." in rel.parts:
        return
    source = source_dir / rel
    if not source.is_file():
        # Missing (or non-file) declared entry: absent from the view; the
        # validator's own rules decide whether absence is an error.
        return
    spot = target / rel
    with suppress(OSError):
        # Source vanishing mid-placement (TOCTOU) or a physically
        # unrepresentable declaration (file/dir prefix collision) likewise
        # leaves the entry absent for the validator to judge.
        spot.parent.mkdir(parents=True, exist_ok=True)
        if spot.is_symlink() or spot.is_file():
            spot.unlink()
        if link:
            with suppress(OSError):
                # Hardlink-unsupported mount: fall through to the copy.
                os.link(source, spot)
                return
        shutil.copy2(source, spot)
