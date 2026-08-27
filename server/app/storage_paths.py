from functools import lru_cache
from pathlib import Path
from typing import Any

from server.app.services.path_hygiene import warn_legacy_absolute

_MANAGED_CATEGORIES = frozenset({"videos", "jobs", "logs", "packages"})


@lru_cache(maxsize=16)
def _resolved_dir(path: Path) -> Path:
    """Resolve a managed root once instead of per stored-path lookup."""
    return path.resolve(strict=True)


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


def resolve_package_file(packages_dir: Path, filename: str) -> Path:
    """Resolve ``filename`` inside ``packages_dir``, rejecting escapes (non-strict)."""
    resolved = (packages_dir / filename).resolve()
    resolved_root = packages_dir.resolve()
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        raise ManagedPathError("Path escapes package root", root_kind="package")
    return resolved


def make_data_relative(path: Path, data_dir: Path) -> str:
    """Return the canonical POSIX path of ``path`` relative to ``data_dir``.

    Both paths are resolved. ``path`` must be a strict descendant of
    ``data_dir``. The returned string uses forward slashes and has no leading
    slash. Examples: ``jobs/demo_workflow/...``, ``logs/...-node.log``,
    ``packages/...``.

    Missing leaf paths are accepted so that not-yet-created log files and run
    directories can be canonicalized before persistence.
    """
    resolved_path = resolve_with_existing_parent(path, allow_missing=True)
    resolved_data_dir = _resolved_dir(data_dir)
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
    resolved_data_dir = _resolved_dir(data_dir)

    if candidate.is_absolute():
        resolved_candidate = resolve_with_existing_parent(candidate, allow_missing=allow_missing)
        if resolved_candidate != resolved_data_dir and resolved_candidate.is_relative_to(
            resolved_data_dir
        ):
            warn_legacy_absolute()
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

        warn_legacy_absolute()
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


def resolve_job_dir(job: Any, jobs_dir: Path) -> Path:
    """Return the job directory, resolving storage_dir safely against jobs_dir.

    ``jobs_dir`` is expected to be a direct child of the project ``data_dir``
    (typically ``data/jobs``).
    """
    # Job records may be partial dicts constructed in tests/services without an
    # ``id`` key, so fall back to "" rather than requiring the key.
    return _resolve_record_dir(job, "", jobs_dir, "job")
