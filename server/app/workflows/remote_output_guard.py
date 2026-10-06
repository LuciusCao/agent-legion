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
remote-channel outputs. The pre-validation snapshot is the digest the
promote phase already streamed and registered for each landed output
(``apply_remote_artifact_refs`` fills ``landed_hashes``; the completion
tail passes it on as ``read_only_outputs``), so validation re-reads each
file only once, after validation on every verdict — raised view-arm
failures included, #939 (codex #913 P2: no extra full read
before validation). Any rewrite or deletion fails the node through the
``Validator error:`` channel (the same family as the declared-inputs
read-only violation), naming the offending outputs, and the diverged
local copies are evicted so readers fall back to the authority object
(``evict_diverged_copies``). Local
(archive-channel) outputs keep the reconcile semantics of
``validation_view`` — their cleaned bytes are what the mirror uploads.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from pathlib import Path

from server.app.services.job_artifact_gzip import file_sha256

logger = logging.getLogger(__name__)


def find_remote_output_rewrites(view_dir: Path, snapshot: Mapping[str, str]) -> dict[str, str]:
    """Map every snapshotted output the validator changed to the change kind.

    ``snapshot`` maps name -> sha256 of the bytes that landed (the promote
    phase's verified digest); the result maps name -> ``"rewritten"`` /
    ``"deleted"`` / ``"unverifiable"``. A full re-hash, never a stat
    fast-path: replacing a file with identical bytes is not flagged, while a
    same-size in-place rewrite within one timestamp tick (unchanged
    size/mtime) is. Each output is checked independently (#939): one that
    cannot be stat-ed or hashed is recorded as unverifiable — treated as diverged — and
    never stops the check of the others.
    """
    kinds = {name: _change_kind(view_dir / name, snapshot[name]) for name in sorted(snapshot)}
    return {name: kind for name, kind in kinds.items() if kind is not None}


def _change_kind(path: Path, digest: str) -> str | None:
    try:
        # #1035: ``is_file()`` only swallows ENOENT-family errors — a stat
        # failing with EIO / NFS ESTALE raises, so it sits inside the
        # per-output isolation too instead of escaping the whole check.
        if not path.is_file():
            return "deleted"
        return None if file_sha256(path) == digest else "rewritten"
    except Exception:
        # #204 broad-except audit: fail-closed — an unstattable or unhashable
        # copy is untrusted (diverged from the authority digest), never a
        # reason to stop checking or evicting the others (#939, #1035).
        return "unverifiable"


def evict_diverged_copies(snapshot: Mapping[str, str], names: Iterable[str], *dirs: Path) -> None:
    """Delete the local copies of ``names`` that no longer match the digest.

    Codex #913 R2 P1: the validation view hardlinks outputs, so an in-place
    rewrite reaches the run view and (through ``link_into_view``) the job
    dir; local-first readers (``JobArtifactService.read`` /
    ``open_raw_artifact``) would then serve bytes that disagree with the
    manifest row. The job dir is an evictable cache (EXEC-ARTIFACT-STORE-001):
    dropping the diverged copy sends readers to the authority object — no
    download-back. Copies still matching the digest (e.g. the job-dir inode a
    replace-family rewrite left alone) stay. Runs only after the verdict is
    final; a copy that cannot be hashed is untrusted and evicted too (#939);
    any eviction failure is logged and never masks that verdict.
    """
    for name in names:
        for base in dict.fromkeys(dirs):
            path = base / name
            try:
                if _change_kind(path, snapshot[name]) in ("rewritten", "unverifiable"):
                    path.unlink()
            except Exception as exc:
                # #204 broad-except audit: log-and-continue — eviction runs
                # after the verdict is final and must never replace it (#939).
                logger.warning("could not evict diverged remote output copy %s: %s", path, exc)


def guard_remote_outputs(
    verdict: str | None, snapshot: Mapping[str, str], view_dir: Path, job_dir: Path
) -> str | None:
    """Run the post-validation remote-output check on ANY verdict (#939).

    Called after validation settles — a passing verdict, the validator's own
    failure, or a raised view arm already converted to ``Validator error:``
    (e.g. a mutated declared input). A rewrite fails the node only when no
    verdict exists yet (the original message wins) and always evicts the
    diverged local copies — an output that cannot be hashed counts as
    diverged (``"unverifiable"``), so one unreadable file never skips the
    eviction of the others. No hashing or eviction error may replace or drop
    an existing verdict.
    """
    rewrites = find_remote_output_rewrites(view_dir, snapshot)
    if rewrites:
        verdict = verdict or remote_output_rewrite_error(rewrites)
        evict_diverged_copies(snapshot, rewrites, view_dir, job_dir)
    return verdict


def remote_output_rewrite_error(changes: Mapping[str, str]) -> str:
    """The node failure message for a validator that touched remote outputs."""
    listed = ", ".join(f"{name!r} ({kind})" for name, kind in changes.items())
    return (
        "Validator error: validator modified remote-channel output(s) "
        f"{listed}; outputs the Worker uploaded straight to object "
        "storage are already the authority copy and are read-only to "
        "scripts/validate_output.py — validate them without writing back (#867)"
    )
