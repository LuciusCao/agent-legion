from pathlib import Path
from typing import Any


class ManagedPathError(ValueError):
    """Raised when a stored path escapes its managed root."""

    def __init__(
        self,
        message: str,
        *,
        record_id: str = "",
        root_kind: str = "managed",
    ) -> None:
        super().__init__(message)
        self.record_id = record_id
        self.root_kind = root_kind


def resolve_with_existing_parent(candidate: Path, *, allow_missing: bool) -> Path:
    """Resolve symlinks in parents; for missing paths resolve the nearest existing parent.

    When ``allow_missing`` is true and the full path does not exist, walk up to
    the longest existing prefix, resolve that prefix strictly (following
    symlinks), and append the remaining suffix. This prevents a symlink parent
    from pointing outside the managed root while still allowing not-yet-created
    leaf paths.
    """
    if candidate.exists() or not allow_missing:
        return candidate.resolve(strict=False)

    parts = candidate.parts
    for i in range(len(parts), 0, -1):
        prefix = Path(*parts[:i])
        if prefix.exists():
            resolved_prefix = prefix.resolve(strict=True)
            suffix = Path(*parts[i:])
            return resolved_prefix / suffix

    # No existing parent (should not happen when the root exists); fall back to
    # a best-effort resolution for the error path.
    return candidate.resolve(strict=False)


def resolve_managed_path(
    root: Path,
    stored_path: str | Path,
    *,
    allow_missing: bool,
    record_id: str = "",
    root_kind: str = "managed",
) -> Path:
    """Resolve a stored path so it stays strictly inside ``root``.

    Relative stored paths are interpreted relative to ``root``. Symlinks in
    parents are followed, and missing leaf paths are accepted only when
    ``allow_missing`` is true. The returned path is never the root itself and
    never outside the root.
    """
    resolved_root = root.resolve(strict=True)
    candidate = Path(stored_path).expanduser()
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = resolve_with_existing_parent(candidate, allow_missing=allow_missing)
    if resolved_candidate == resolved_root or not resolved_candidate.is_relative_to(resolved_root):
        message = f"Path escapes {root_kind} root"
        if record_id:
            message = f"{message} for record {record_id}"
        raise ManagedPathError(
            message,
            record_id=record_id,
            root_kind=root_kind,
        )
    return resolved_candidate


def resolve_video_dir(video: Any, videos_dir: Path) -> Path:
    """Return the video directory, resolving storage_dir safely against videos_dir."""
    storage_dir: str = video.get("storage_dir", "")
    video_id: str = video["id"]
    if storage_dir:
        return resolve_managed_path(
            videos_dir,
            storage_dir,
            allow_missing=True,
            record_id=video_id,
            root_kind="video",
        )
    return videos_dir / video_id


def resolve_job_dir(job: Any, jobs_dir: Path) -> Path:
    """Return the job directory, resolving storage_dir safely against jobs_dir."""
    storage_dir: str = job.get("storage_dir", "")
    job_id: str = job.get("id", "")
    if storage_dir:
        return resolve_managed_path(
            jobs_dir,
            storage_dir,
            allow_missing=True,
            record_id=job_id,
            root_kind="job",
        )
    return jobs_dir / job_id
