"""Contract tests for the directory identity primitives (SECURITY-PATH-002, #1097)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from server.app.fs_dir_identity import (
    DirectoryIdentityError,
    create_dir_identity,
    dir_identity_at,
    open_dir_identity_at,
    require_dir_identity_at,
)
from server.app.fs_dir_moves import promote_dir_at, remove_tree_at, rename_at, retire_dir_at
from server.app.fs_safety import PathEscapeError, open_dir_beneath

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


@pytest.mark.parametrize("occupant", ["none", "empty-dir", "dir", "symlink", "file"])
def test_promote_requires_an_absent_target(parent: int, tmp_path: Path, occupant: str) -> None:
    identity = dir_identity_at(parent, "real")
    assert identity is not None
    target = tmp_path / "target"
    if occupant == "empty-dir":
        target.mkdir()
    elif occupant == "dir":
        (target / "keep").mkdir(parents=True)
    elif occupant == "symlink":
        target.symlink_to(tmp_path / "other", target_is_directory=True)
    elif occupant == "file":
        target.write_text("x", encoding="utf-8")
    if occupant == "none":
        promote_dir_at(parent, "real", identity, "target")
        assert dir_identity_at(parent, "target") == identity
        return
    with pytest.raises(DirectoryIdentityError):
        promote_dir_at(parent, "real", identity, "target")
    assert dir_identity_at(parent, "real") == identity  # nothing moved
    with pytest.raises(DirectoryIdentityError):  # wrong source identity
        promote_dir_at(parent, "other", identity, "missing")


def test_remove_tree_only_removes_the_expected_directory(parent: int, tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f.txt").write_text("x", encoding="utf-8")
    (tree / "link").symlink_to(tmp_path / "other", target_is_directory=True)
    (tmp_path / "other" / "precious").write_text("keep", encoding="utf-8")
    identity = create_dir_identity(tmp_path / "fresh")
    assert remove_tree_at(parent, "fresh", identity) is True
    tree_id = dir_identity_at(parent, "tree")
    assert tree_id is not None
    assert remove_tree_at(parent, "other", tree_id) is False  # foreign: kept
    assert remove_tree_at(parent, "missing", tree_id) is False
    assert remove_tree_at(parent, "tree", tree_id) is True
    assert not tree.exists()
    assert (tmp_path / "other" / "precious").read_text(encoding="utf-8") == "keep"


def test_open_dir_beneath_accepts_a_pinned_descriptor_root(parent: int) -> None:
    with open_dir_beneath(parent, ["real"]) as fd:
        assert os.fstat(fd).st_ino == os.lstat("real", dir_fd=parent).st_ino
    os.fstat(parent)  # the caller's descriptor stays open
    with pytest.raises(OSError), open_dir_beneath(parent, ["link"]):
        pass
