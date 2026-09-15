"""Plan-time primitives for shared-material propagation (issue #673).

Split from ``skill_shared_propagate`` for the file-size budget: the
per-skill result types, the tag computation and the generation
fingerprint a propagate batch is planned from (and re-verified against
before every save — codex P1/P2).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from server.app.services import skill_repo
from server.app.services.job_errors import ConflictError
from server.app.services.skill_shared_store import MAP_PATH

_VERSION_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
INITIAL_VERSION_TAG = "v0.1.0"
UNREADABLE = "<unreadable>"

PropagateStatus = Literal["synced", "skipped", "failed"]


@dataclass(frozen=True)
class PropagateSkillResult:
    skill: str
    status: PropagateStatus
    tag: str | None = None
    detail: str | None = None
    synced_files: tuple[str, ...] = ()


class SharedGenerationConflictError(ConflictError):
    """Mid-batch generation swap (409). Each completed skill's commit is
    atomic and valid on its own and the retry converges, but the caller
    must not be blind to what already landed: the 409 payload carries the
    per-skill results completed BEFORE the swap, in the same shape as the
    normal response's ``results`` (codex on #674)."""

    def __init__(self, message: str, completed: Sequence[PropagateSkillResult]) -> None:
        super().__init__(message)
        self.payload = {
            "message": message,
            "results": [
                {**asdict(result), "synced_files": list(result.synced_files)}
                for result in completed
            ],
        }


@dataclass(frozen=True)
class PropagateResult:
    results: tuple[PropagateSkillResult, ...]


@dataclass(frozen=True)
class SharedGeneration:
    """Content fingerprint of the shared state a batch was planned from:
    the map.json digest plus every mapped source's digest."""

    map_digest: str
    source_digests: tuple[tuple[str, str], ...]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_shared_source_bytes(shared_dir: Path, source: str) -> bytes:
    """Plan-time source read WITH containment (codex P1, same rule as the
    viewer): resolve and require the target to stay inside the resolved
    ``_shared`` — an intermediate symlink must not smuggle host files into
    skill repos. Raises OSError for unreadable OR escaping sources. A
    symlinked ``_shared`` — or a symlinked workspace dir above it (codex
    P1 on #674) — is rejected too: the link target would otherwise become
    the trusted root."""
    if shared_dir.is_symlink() or shared_dir.parent.is_symlink():
        raise OSError("_shared and its workspace dir must be real directories, not symlinks")
    root = shared_dir.resolve()
    target = (root / source).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise OSError(f"source {source!r} escapes _shared via a symlink") from exc
    return target.read_bytes()


def source_digest(shared_dir: Path, source: str) -> str:
    try:
        return _digest(read_shared_source_bytes(shared_dir, source))
    except OSError:
        return UNREADABLE


def read_generation(shared_dir: Path, sources: Sequence[str]) -> SharedGeneration:
    """Must be called under the ``_shared`` edit lock."""
    return SharedGeneration(
        map_digest=_digest((shared_dir / MAP_PATH).read_bytes()),
        source_digests=tuple((source, source_digest(shared_dir, source)) for source in sources),
    )


def read_source_bytes(shared_dir: Path, sources: Sequence[str]) -> dict[str, bytes]:
    """Bytes of every readable source (plan-time pin; unreadable omitted)."""
    out: dict[str, bytes] = {}
    for source in sources:
        try:
            out[source] = read_shared_source_bytes(shared_dir, source)
        except OSError:
            continue
    return out


def generation_matches(shared_dir: Path, generation: SharedGeneration) -> bool:
    if _digest((shared_dir / MAP_PATH).read_bytes()) != generation.map_digest:
        return False
    return all(
        source_digest(shared_dir, source) == digest for source, digest in generation.source_digests
    )


def next_version_tag(tags: Sequence[str]) -> str:
    """Highest ``vX.Y.Z`` tag with patch +1; ``v0.1.0`` when none parse."""
    best: tuple[int, int, int] | None = None
    for tag in tags:
        match = _VERSION_TAG_RE.fullmatch(tag)
        if match is None:
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if best is None or version > best:
            best = version
    if best is None:
        return INITIAL_VERSION_TAG
    return f"v{best[0]}.{best[1]}.{best[2] + 1}"


def next_repo_tag(repo_dir: Path) -> str:
    """In-lock tag selection (codex P2): read the repo's tags and bump."""
    return next_version_tag(skill_repo.list_tags(repo_dir))
