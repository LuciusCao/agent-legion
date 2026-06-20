from pathlib import Path

import pytest

from server.app.services.package_deletion import (
    PackageDeletionService,
    PackageLockedError,
    PackageNotFoundError,
)
from server.app.storage_paths import ManagedPathError


def _insert_package(db, path: Path, *, locked: int = 0) -> int:
    db.insert_package(str(path), locked=locked)
    return int(db.list_packages(limit=1)[0]["id"])


def test_delete_removes_existing_file_and_record(db, tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir(exist_ok=True)
    package_path = packages_dir / "batch.zip"
    package_path.write_bytes(b"archive")
    package_id = _insert_package(db, package_path)

    PackageDeletionService(db, packages_dir).delete(package_id)

    assert not package_path.exists()
    assert db.list_packages(limit=1000) == []


def test_delete_removes_record_when_file_is_missing(db, tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir(exist_ok=True)
    package_id = _insert_package(db, packages_dir / "missing.zip")

    PackageDeletionService(db, packages_dir).delete(package_id)

    assert db.list_packages(limit=1000) == []


def test_delete_rejects_missing_record(db, tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir(exist_ok=True)

    with pytest.raises(PackageNotFoundError):
        PackageDeletionService(db, packages_dir).delete(999)


def test_delete_rejects_locked_record_without_changes(db, tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir(exist_ok=True)
    package_path = packages_dir / "locked.zip"
    package_path.write_bytes(b"archive")
    package_id = _insert_package(db, package_path, locked=1)

    with pytest.raises(PackageLockedError):
        PackageDeletionService(db, packages_dir).delete(package_id)

    assert package_path.read_bytes() == b"archive"
    assert [package["id"] for package in db.list_packages(limit=1000)] == [package_id]


def test_delete_rejects_symlink_to_outside_without_changes(db, tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir(exist_ok=True)
    outside_path = tmp_path / "outside.zip"
    outside_path.write_bytes(b"outside archive")
    package_path = packages_dir / "escaped.zip"
    package_path.symlink_to(outside_path)
    package_id = _insert_package(db, package_path)

    with pytest.raises(ManagedPathError):
        PackageDeletionService(db, packages_dir).delete(package_id)

    assert outside_path.read_bytes() == b"outside archive"
    assert package_path.is_symlink()
    assert [package["id"] for package in db.list_packages(limit=1000)] == [package_id]


def test_delete_removes_existing_file_with_relative_path(db, tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir(exist_ok=True)
    package_path = packages_dir / "batch.zip"
    package_path.write_bytes(b"archive")
    db.insert_package("packages/batch.zip")
    package_id = int(db.list_packages(limit=1)[0]["id"])

    PackageDeletionService(db, packages_dir).delete(package_id)

    assert not package_path.exists()
    assert db.list_packages(limit=1000) == []
