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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from server.app.services import skill_repo
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


def source_digest(shared_dir: Path, source: str) -> str:
    try:
        return _digest((shared_dir / source).read_bytes())
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
            out[source] = (shared_dir / source).read_bytes()
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
