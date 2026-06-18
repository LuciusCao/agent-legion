from pathlib import Path

from server.app.db import Database
from server.app.storage_paths import resolve_managed_path


class PackageNotFoundError(LookupError):
    """Raised when a package record cannot be found."""


class PackageLockedError(RuntimeError):
    """Raised when a locked package cannot be deleted."""


class PackageDeletionService:
    def __init__(self, db: Database, packages_dir: Path) -> None:
        self.db = db
        self.packages_dir = packages_dir

    def delete(self, package_id: int) -> None:
        package = next(
            (item for item in self.db.list_packages(limit=1000) if item["id"] == package_id),
            None,
        )
        if package is None:
            raise PackageNotFoundError(f"Package {package_id} not found")
        if package["locked"]:
            raise PackageLockedError(f"Package {package_id} is locked")

        package_path = resolve_managed_path(
            self.packages_dir,
            package["path"],
            allow_missing=True,
            record_id=str(package_id),
            root_kind="package",
        )
        if package_path.exists():
            package_path.unlink()
        self.db.delete_package(package_id)
