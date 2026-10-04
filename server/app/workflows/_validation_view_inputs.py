"""Declared-input byte-source resolution for the validation view (#828/#830/#833).

Split from ``_validation_view_files`` for the file-size budget — this module
owns WHICH bytes a declared input validates against; the placement mechanics
that then isolate those bytes (reflink-or-copy private inode) live there. The
rules, pinned by tests/workflows/test_output_validation_view.py and
tests/db/test_completion_view_inputs.py:

- the dispatch-frozen CAS copy wins (``stage_agent_inputs`` froze what the
  Worker actually consumed; a parallel producer may overwrite the same-name
  job-dir file between dispatch and completion — codex P1);
- no ref, a non-CAS ref shape (claim-time presigned dict), no store root, or
  a missing blob (GC race / stale ref) all fall back to the job dir — the
  pre-#833 exposure, with the validator's own rules judging absence. The
  fallback is NOT a normal channel (EXEC-INPUT-IDENTITY-001,
  docs/architecture/execution-generation.md §2.11): the no-ref arm is a
  legacy exemption serving only pre-#833 manifests without a frozen ref and
  drains to zero as old jobs exhaust — every new manifest carries frozen
  refs; the blob-missing arm is the fail-open GC-race degradation, rare by
  construction ((job,node) refs shield the blob for the job's lifetime);
- the refs map keys: post-#876-P2-a manifests record the NORMALIZED name
  (``stage_agent_inputs`` dedupes aliases at the freeze point); pre-dedup
  legacy manifests record the raw declared spelling. Lookup tries the raw
  alias first (legacy hit, and the exact alias the legacy Worker consumed),
  then the normalized name (new manifests), then the job dir — see
  ``resolve_input_source``; the view placement uses the normalized
  ``safe_relative`` name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InputAuthority:
    """The dispatch-frozen input-bytes channel.

    ``refs`` is the manifest's ``input_artifacts`` map (normalized name → ref
    for post-#876-P2-a manifests, raw declared spelling for pre-dedup legacy
    ones); ``artifact_root`` is the CAS root. Either may be absent (legacy
    manifest, no store on the caller) — resolution then falls back to the
    job dir.
    """

    refs: Mapping[str, Any]
    artifact_root: Path | None


def resolve_input_source(
    raw: str, rel: str, authority: InputAuthority | None, input_source: Path
) -> Path:
    """The bytes a declared input validates against (rules see module docstring).

    The job-dir return at the end is the legacy/degradation fallback, never
    the normal channel: reached only by pre-#833 manifests (no frozen ref,
    draining to zero), non-CAS ref shapes, a missing store, or a missing
    blob (EXEC-INPUT-IDENTITY-001).
    """
    if authority is not None and authority.artifact_root is not None:
        digest = _cas_digest(authority.refs.get(raw))
        if digest is None:
            # 冻结点去重后的新 manifest 以归一化名为 ref 键（#876 P2-a）
            # ——声明别名（如 ./in.json）的 raw 查不到时按归一化名再查；
            # legacy manifest 的 raw 键第一查已命中。
            digest = _cas_digest(authority.refs.get(rel))
        if digest is not None:
            # Local import: keeps the pool worker's import graph free of the
            # DB-touching store module until a CAS ref actually resolves.
            from server.app.services.artifact_store import ArtifactNotFoundError, open_blob

            try:
                return open_blob(authority.artifact_root, digest)
            except ArtifactNotFoundError:
                pass
    return input_source / rel


def _cas_digest(ref: Any) -> str | None:
    """A ``sha256:<digest>`` ref's digest; other shapes (claim-time presigned
    dicts) return None and take the job-dir fallback."""
    if isinstance(ref, str) and ref.startswith("sha256:"):
        return ref.split(":", 1)[-1]
    return None
