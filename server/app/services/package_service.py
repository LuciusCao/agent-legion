from pathlib import Path
from typing import Any

from server.app.db import Database
from server.app.storage_paths import ManagedPathError, resolve_data_path


class PackageNotFoundError(LookupError):
    """Raised when a package record cannot be found."""


class PackageLockedError(RuntimeError):
    """Raised when a locked package cannot be deleted."""


def resolve_package_path(
    stored_path: str | Path,
    data_dir: Path,
    packages_dir: Path,
    *,
    record_id: str = "",
) -> Path:
    """Resolve a stored package path so it stays strictly inside ``packages_dir``.

    The path is first resolved against ``data_dir`` (the managed data root) and
    then narrowed to the package root. Escapes raise ``ManagedPathError`` with
    ``root_kind="package"``.
    """
    try:
        resolved = resolve_data_path(stored_path, data_dir, allow_missing=True)
    except ManagedPathError as exc:
        message = str(exc)
        if record_id:
            message = f"{message} for record {record_id}"
        raise ManagedPathError(
            message,
            record_id=record_id,
            root_kind="package",
        ) from exc
    resolved_packages_dir = packages_dir.resolve(strict=True)
    if resolved == resolved_packages_dir or not resolved.is_relative_to(resolved_packages_dir):
        message = "Path escapes package root"
        if record_id:
            message = f"{message} for record {record_id}"
        raise ManagedPathError(
            message,
            record_id=record_id,
            root_kind="package",
        )
    return resolved


def resolve_package_file(packages_dir: Path, filename: str) -> Path:
    """Resolve ``filename`` inside ``packages_dir``, rejecting escapes.

    Resolution is non-strict so a missing packages directory or file is left
    for the caller to handle as "not found".
    """
    resolved = (packages_dir / filename).resolve()
    resolved_root = packages_dir.resolve()
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        raise ManagedPathError("Path escapes package root", root_kind="package")
    return resolved


class PackageService:
    def __init__(self, db: Database, packages_dir: Path) -> None:
        self.db = db
        self.packages_dir = packages_dir

    def get(self, package_id: int) -> dict[str, Any]:
        package = self.db.get_package(package_id)
        if package is None:
            raise PackageNotFoundError(f"Package {package_id} not found")
        return package

    def update(
        self,
        package_id: int,
        *,
        name: str | None = None,
        locked: bool | None = None,
    ) -> None:
        self.get(package_id)
        if name is not None:
            self.db.update_package_name(package_id, name)
        if locked is not None:
            self.db.update_package_stats(package_id, locked=1 if locked else 0)

    def delete(self, package_id: int) -> None:
        package = self.get(package_id)
        if package["locked"]:
            raise PackageLockedError(f"Package {package_id} is locked")

        package_path = resolve_package_path(
            package["path"],
            self.packages_dir.parent,
            self.packages_dir,
            record_id=str(package_id),
        )
        if package_path.exists():
            package_path.unlink()
        self.db.delete_package(package_id)
