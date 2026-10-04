"""Remote-channel outputs are read-only to the Host-side validator (#867).

A Worker that returns an output as a dict-form S3 ref has already had its
bytes promoted to the authority object (``apply_worker_artifact_refs``)
before Host-side validation runs, and the post-validation mirror skips
those names (``skip=remote_names``). A validator that cleans or rewrites
such a file would therefore change only the local copy: the node would
complete on the rewritten bytes while downstream hydration from object
storage — and the manifest row's content hash — still carry the
pre-validation bytes.

The contract (EXEC-VALIDATION-001, chosen over re-promoting rewritten bytes
because no in-tree validator rewrites outputs): validators must not modify
remote-channel outputs. ``worker_output_validation`` hashes every landed
remote output (the completion tail passes them as ``read_only_outputs``)
before validation and re-hashes after; any rewrite or deletion fails the
node through the ``Validator error:`` channel (the same family as the
declared-inputs read-only violation), naming the offending outputs. Local
(archive-channel) outputs keep the reconcile semantics of
``validation_view`` — their cleaned bytes are what the mirror uploads.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from server.app.services.job_artifact_gzip import file_sha256


def snapshot_remote_outputs(view_dir: Path, names: Iterable[str]) -> dict[str, str]:
    """sha256 of each landed remote-channel output, keyed by name.

    Names absent from the view are skipped: there is nothing local for a
    validator to rewrite, and the missing-output check already judged them.
    """
    snapshot: dict[str, str] = {}
    for name in names:
        path = view_dir / name
        if path.is_file():
            snapshot[name] = file_sha256(path)
    return snapshot


def find_remote_output_rewrites(view_dir: Path, snapshot: Mapping[str, str]) -> list[str]:
    """Describe every snapshotted output the validator rewrote or deleted.

    Compares content hashes (not inode/mtime) so a validator that replaces
    a file with identical bytes is not flagged, while a same-size in-place
    rewrite within one timestamp tick still is.
    """
    changes: list[str] = []
    for name in sorted(snapshot):
        path = view_dir / name
        if not path.is_file():
            changes.append(f"{name!r} (deleted)")
        elif file_sha256(path) != snapshot[name]:
            changes.append(f"{name!r} (rewritten)")
    return changes


def remote_output_rewrite_error(changes: list[str]) -> str:
    """The node failure message for a validator that touched remote outputs."""
    return (
        "Validator error: validator modified remote-channel output(s) "
        f"{', '.join(changes)}; outputs the Worker uploaded straight to object "
        "storage are already the authority copy and are read-only to "
        "scripts/validate_output.py — validate them without writing back (#867)"
    )
