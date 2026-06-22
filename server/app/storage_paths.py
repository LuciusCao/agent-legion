import warnings
from pathlib import Path
from typing import Any

_MANAGED_CATEGORIES = frozenset({"videos", "jobs", "logs", "packages"})


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

    Only ``FileNotFoundError`` is handled here; other OS-level errors such as
    ``PermissionError`` or symlink loops (``RuntimeError``) propagate to the
    caller.
    """
    if allow_missing:
        # ``strict=False`` still resolves symlink components, including a broken
        # symlink whose target does not exist. That is essential for detecting a
        # stored path that escapes through such a parent.
        return candidate.resolve(strict=False)
    try:
        return candidate.resolve(strict=True)
    except FileNotFoundError:
        raise


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
    try:
        resolved_candidate = resolve_with_existing_parent(candidate, allow_missing=allow_missing)
    except FileNotFoundError as exc:
        message = f"Path does not exist inside {root_kind} root"
        if record_id:
            message = f"{message} for record {record_id}"
        raise ManagedPathError(
            message,
            record_id=record_id,
            root_kind=root_kind,
        ) from exc
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


def make_data_relative(path: Path, data_dir: Path) -> str:
    """Return the canonical POSIX path of ``path`` relative to ``data_dir``.

    Both paths are resolved. ``path`` must be a strict descendant of
    ``data_dir``. The returned string uses forward slashes and has no leading
    slash. Examples: ``videos/knowledge_x41020501``,
    ``jobs/question_comprehension/...``, ``logs/...-download.log``,
    ``packages/...``.

    Missing leaf paths are accepted so that not-yet-created log files and run
    directories can be canonicalized before persistence.
    """
    resolved_path = resolve_with_existing_parent(path, allow_missing=True)
    resolved_data_dir = data_dir.resolve(strict=True)
    if resolved_path == resolved_data_dir or not resolved_path.is_relative_to(resolved_data_dir):
        raise ManagedPathError("Path is not inside data directory")
    return resolved_path.relative_to(resolved_data_dir).as_posix()


def resolve_data_path(
    stored_path: str | Path,
    data_dir: Path,
    *,
    allow_missing: bool,
) -> Path:
    """Resolve a stored path so it stays strictly inside ``data_dir``.

    Relative paths are interpreted below ``data_dir``. Absolute paths already
    inside ``data_dir`` are accepted for backwards compatibility with a
    deprecation warning. Absolute paths outside ``data_dir`` are rebased only
    when their suffix matches ``<data-dir-name>/<managed-category>/...``;
    every component beginning with the category is preserved. Other absolute
    paths are rejected.
    """
    if not stored_path:
        raise ManagedPathError("Stored path is empty", root_kind="data")

    candidate = Path(stored_path).expanduser()
    resolved_data_dir = data_dir.resolve(strict=True)

    if candidate.is_absolute():
        resolved_candidate = resolve_with_existing_parent(candidate, allow_missing=allow_missing)
        if resolved_candidate != resolved_data_dir and resolved_candidate.is_relative_to(
            resolved_data_dir
        ):
            warnings.warn(
                "Legacy absolute path stored; resolving relative to data_dir instead",
                DeprecationWarning,
                stacklevel=2,
            )
            return resolved_candidate

        parts = resolved_candidate.parts
        data_dir_name = resolved_data_dir.name
        suffix_parts: list[str] = []
        for i, part in enumerate(parts):
            if part == data_dir_name and i + 1 < len(parts) and parts[i + 1] in _MANAGED_CATEGORIES:
                suffix_parts = list(parts[i + 1 :])
                break

        if not suffix_parts:
            raise ManagedPathError(
                "Absolute path cannot be mapped unambiguously inside data directory",
                root_kind="data",
            )

        warnings.warn(
            "Legacy absolute path stored; resolving relative to data_dir instead",
            DeprecationWarning,
            stacklevel=2,
        )
        candidate = resolved_data_dir.joinpath(*suffix_parts)
    else:
        candidate = resolved_data_dir / candidate

    resolved_candidate = resolve_with_existing_parent(candidate, allow_missing=allow_missing)
    if resolved_candidate == resolved_data_dir or not resolved_candidate.is_relative_to(
        resolved_data_dir
    ):
        raise ManagedPathError("Path escapes data directory", root_kind="data")
    return resolved_candidate


def _resolve_record_dir(
    record: Any,
    record_id_key: str,
    managed_dir: Path,
    root_kind: str,
) -> Path:
    """Resolve a record's storage_dir against the data root, then narrow to the managed root.

    ``managed_dir`` must be a direct child of ``data_dir`` (e.g. ``data/videos``).
    """
    storage_dir: str = record.get("storage_dir", "")
    record_id: str = record[record_id_key] if record_id_key else record.get("id", "")
    if not storage_dir:
        return managed_dir / record_id

    data_dir = managed_dir.parent
    try:
        resolved = resolve_data_path(storage_dir, data_dir, allow_missing=True)
    except ManagedPathError as exc:
        message = str(exc)
        if record_id:
            message = f"{message} for record {record_id}"
        raise ManagedPathError(
            message,
            record_id=record_id,
            root_kind=root_kind,
        ) from exc

    resolved_managed_dir = managed_dir.resolve(strict=True)
    if resolved == resolved_managed_dir or not resolved.is_relative_to(resolved_managed_dir):
        message = f"Path escapes {root_kind} root"
        if record_id:
            message = f"{message} for record {record_id}"
        raise ManagedPathError(
            message,
            record_id=record_id,
            root_kind=root_kind,
        )
    return resolved


def resolve_video_dir(video: Any, videos_dir: Path) -> Path:
    """Return the video directory, resolving storage_dir safely against videos_dir.

    ``videos_dir`` is expected to be a direct child of the project ``data_dir``
    (typically ``data/videos``).
    """
    return _resolve_record_dir(video, "id", videos_dir, "video")


def resolve_job_dir(job: Any, jobs_dir: Path) -> Path:
    """Return the job directory, resolving storage_dir safely against jobs_dir.

    ``jobs_dir`` is expected to be a direct child of the project ``data_dir``
    (typically ``data/jobs``).
    """
    # Job records may be partial dicts constructed in tests/services without an
    # ``id`` key, so fall back to "" rather than requiring the key.
    return _resolve_record_dir(job, "", jobs_dir, "job")


def derive_run_dir_from_log_path(
    log_path: str | Path,
    node_key: str,
    job_id: str,
    jobs_dir: Path,
) -> Path | None:
    """Find the Pi token directory from the legacy log file path.

    Pi artifacts live under ``jobs/<workspace>/<job_id>/runs/<node_key>/<token>/``
    while the log file is stored at ``logs/jobs/<job_id>-<node_key>.log``. When the
    executor result does not include ``run_dir``, we scan ``jobs_dir`` for the
    workspace that contains this ``job_id`` and pick the most recently modified
    token directory.
    """
    if not log_path or not node_key or not job_id:
        return None
    if not jobs_dir.is_dir():
        return None

    job_dir: Path | None = None
    for workspace_dir in jobs_dir.iterdir():
        if not workspace_dir.is_dir():
            continue
        candidate = workspace_dir / job_id
        if candidate.is_dir():
            job_dir = candidate
            break
    if job_dir is None:
        return None

    run_parent = job_dir / "runs" / node_key
    if not run_parent.is_dir():
        return None
    token_dirs = [d for d in run_parent.iterdir() if d.is_dir()]
    if not token_dirs:
        return None
    return max(token_dirs, key=lambda p: p.stat().st_mtime)


def derive_session_dir_from_run_dir(run_dir: Path | None) -> Path | None:
    if run_dir is None:
        return None
    session_dir = run_dir / "session"
    return session_dir if session_dir.is_dir() else None
