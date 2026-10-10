"""Contract tests for the directory identity primitives (SECURITY-PATH-002, #1097)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from server.app.fs_dir_identity import (
    DirectoryIdentityError,
    dir_identity_at,
    open_dir_identity_at,
    rename_at,
    require_dir_identity_at,
    retire_dir_at,
)
from server.app.fs_safety import PathEscapeError

pytestmark = pytest.mark.no_db


@pytest.fixture
def parent(tmp_path: Path) -> Iterator[int]:
    (tmp_path / "real").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "file").write_text("x", encoding="utf-8")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield fd
    finally:
        os.close(fd)


def test_identity_only_for_real_directories(parent: int) -> None:
    real = os.lstat("real", dir_fd=parent)
    assert dir_identity_at(parent, "real") == (real.st_dev, real.st_ino)
    assert dir_identity_at(parent, "missing") is None
    for name in ("link", "file"):
        with pytest.raises(DirectoryIdentityError, match="not a real directory"):
            dir_identity_at(parent, name)


@pytest.mark.parametrize("name", ["", "..", "a/b"])
def test_names_are_single_components(parent: int, name: str) -> None:
    with pytest.raises(PathEscapeError):
        dir_identity_at(parent, name)


def test_require_and_open_refuse_another_directory(parent: int) -> None:
    identity = dir_identity_at(parent, "real")
    assert identity is not None
    require_dir_identity_at(parent, "real", identity)
    with open_dir_identity_at(parent, "real", identity) as fd:
        assert os.fstat(fd).st_ino == identity[1]
    for name in ("other", "missing"):
        with pytest.raises(DirectoryIdentityError):
            require_dir_identity_at(parent, name, identity)
    with pytest.raises(DirectoryIdentityError), open_dir_identity_at(parent, "other", identity):
        pass
    with pytest.raises(OSError), open_dir_identity_at(parent, "link", identity):
        pass  # O_NOFOLLOW: never opens through the link


def test_retire_moves_only_the_expected_directory(parent: int) -> None:
    identity = dir_identity_at(parent, "real")
    assert identity is not None
    retire_dir_at(parent, "real", identity, "retired")
    assert dir_identity_at(parent, "retired") == identity
    assert dir_identity_at(parent, "real") is None
    rename_at(parent, "retired", "real")
    with pytest.raises(DirectoryIdentityError):
        retire_dir_at(parent, "other", identity, "retired")
    assert dir_identity_at(parent, "retired") is None  # nothing moved
