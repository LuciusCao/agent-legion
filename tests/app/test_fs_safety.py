"""Contract tests for the shared containment primitives (SECURITY-PATH-002, #928)."""

from __future__ import annotations

import errno
import os
import re
from pathlib import Path, PurePosixPath

import pytest

from server.app import fs_safety
from server.app.fs_safety import (
    NotRegularFileError,
    PathEscapeError,
    create_new_at,
    open_dir_beneath,
    open_dir_nofollow,
    open_regular_at,
    relative_parts,
    resolve_within,
)

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules switched onto fs_safety. Each must keep using the shared primitive
# rather than reintroducing a hand-rolled containment check (see the PR #928
# inventory for the sites still pending a later batch).
MIGRATED_MODULES = (
    "server/app/mcp_server/local_files.py",
    "server/app/studio_chat/task_metadata_files.py",
    "server/app/studio_chat/kimi_task_snapshot.py",
    "server/app/studio_chat/kimi_task_store.py",
    "server/app/spa.py",
    "server/app/services/job_artifacts.py",
    "server/app/services/skill_shared_view.py",
    "server/app/services/skill_shared_put.py",
    "server/app/services/skill_shared_sync.py",
    "server/app/services/skill_shared_propagate_plan.py",
    "server/app/services/skill_edit_checks.py",
    "server/app/services/skill_catalog.py",
    "server/app/services/skill_editing.py",
)
_HAND_ROLLED = (
    re.compile(r"is_relative_to\("),
    re.compile(r"\bO_NOFOLLOW\b"),
    re.compile(r"commonpath\("),
    # resolve-then-relative_to containment: relative_to(...) guarded by ValueError
    re.compile(r"\.relative_to\([^)]*\)\s*\n\s*except ValueError"),
)


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "sub").mkdir(parents=True)
    outside.mkdir()
    (root / "sub" / "file.txt").write_text("inside")
    (outside / "secret.txt").write_text("outside")
    return root, outside


# ---- lexical gate ---------------------------------------------------------


@pytest.mark.parametrize("name", ["a", "a/b.txt", PurePosixPath("x/y")])
def test_relative_parts_accepts_plain_relative_names(name: str | PurePosixPath) -> None:
    assert relative_parts(name) == PurePosixPath(name).parts


@pytest.mark.parametrize("name", ["", ".", "/etc/passwd", "../x", "a/../../x", "a/.."])
def test_relative_parts_rejects_empty_absolute_and_parent_segments(name: str) -> None:
    with pytest.raises(PathEscapeError):
        relative_parts(name)


# ---- resolution tier ------------------------------------------------------


def test_resolve_within_returns_resolved_inside_path(tree: tuple[Path, Path]) -> None:
    root, _ = tree
    assert resolve_within(root, "sub/file.txt") == (root / "sub" / "file.txt").resolve()
    assert resolve_within(root, "sub/missing.txt").name == "missing.txt"


@pytest.mark.parametrize("name", ["../outside/secret.txt", "sub/../../outside"])
def test_resolve_within_rejects_parent_escape(tree: tuple[Path, Path], name: str) -> None:
    root, _ = tree
    with pytest.raises(PathEscapeError):
        resolve_within(root, name)


def test_resolve_within_absolute_name_is_contained_only_when_inside(
    tree: tuple[Path, Path],
) -> None:
    root, outside = tree
    with pytest.raises(PathEscapeError):
        resolve_within(root, str(outside / "secret.txt"))
    inside = str(root / "sub" / "file.txt")
    assert resolve_within(root, inside) == Path(inside).resolve()


def test_resolve_within_rejects_symlink_escape_and_keeps_inside_links(
    tree: tuple[Path, Path],
) -> None:
    root, outside = tree
    (root / "out").symlink_to(outside, target_is_directory=True)
    (root / "alias").symlink_to(root / "sub", target_is_directory=True)
    with pytest.raises(PathEscapeError):
        resolve_within(root, "out/secret.txt")
    assert resolve_within(root, "alias/file.txt") == (root / "sub" / "file.txt").resolve()


def test_resolve_within_root_requires_opt_in(tree: tuple[Path, Path]) -> None:
    root, _ = tree
    with pytest.raises(PathEscapeError):
        resolve_within(root, "sub/..")
    assert resolve_within(root, "sub/..", allow_root=True) == root.resolve()


def test_resolve_within_accepts_symlinked_root(tree: tuple[Path, Path], tmp_path: Path) -> None:
    root, _ = tree
    link = tmp_path / "root-link"
    link.symlink_to(root, target_is_directory=True)
    assert resolve_within(link, "sub/file.txt") == (root / "sub" / "file.txt").resolve()


# ---- descriptor tier ------------------------------------------------------


def _read(parent: int, name: str) -> str:
    fd = open_regular_at(parent, name)
    with os.fdopen(fd, "rb") as handle:
        return handle.read().decode()


def test_open_dir_beneath_walks_real_directories(tree: tuple[Path, Path]) -> None:
    root, _ = tree
    with open_dir_beneath(root, ["sub"]) as fd:
        assert _read(fd, "file.txt") == "inside"


def test_open_dir_beneath_refuses_symlinked_component_even_inside(
    tree: tuple[Path, Path],
) -> None:
    root, outside = tree
    (root / "out").symlink_to(outside, target_is_directory=True)
    (root / "alias").symlink_to(root / "sub", target_is_directory=True)
    for name in ("out", "alias"):
        with pytest.raises(OSError) as info, open_dir_beneath(root, [name]):
            pass
        assert info.value.errno in (errno.ELOOP, errno.ENOTDIR)


@pytest.mark.parametrize("part", ["", ".", "..", "a/b", "a\x00b"])
def test_open_dir_beneath_rejects_non_component_names(tree: tuple[Path, Path], part: str) -> None:
    root, _ = tree
    with pytest.raises(PathEscapeError), open_dir_beneath(root, [part]):
        pass


def test_open_dir_beneath_follows_trusted_root_link(
    tree: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = tree
    link = tmp_path / "root-link"
    link.symlink_to(root, target_is_directory=True)
    with open_dir_beneath(link, ["sub"]) as fd:
        assert _read(fd, "file.txt") == "inside"


def test_open_dir_beneath_creates_missing_components(tree: tuple[Path, Path]) -> None:
    root, _ = tree
    with open_dir_beneath(root, ["new", "deeper"], create=True, mode=0o700) as fd:
        assert os.listdir(fd) == []
    assert (root / "new" / "deeper").is_dir()
    assert (root / "new").stat().st_mode & 0o777 == 0o700


def test_pinned_descriptor_survives_directory_swap(tree: tuple[Path, Path]) -> None:
    """A component replaced by a link after the walk cannot redirect pinned I/O."""
    root, outside = tree
    with open_dir_beneath(root, ["sub"]) as fd:
        (root / "sub").rename(root / "moved")
        (root / "sub").symlink_to(outside, target_is_directory=True)
        assert _read(fd, "file.txt") == "inside"
        with pytest.raises(FileNotFoundError):
            open_regular_at(fd, "secret.txt")
    with pytest.raises(OSError), open_dir_beneath(root, ["sub"]):
        pass


def test_open_dir_nofollow_rejects_relative_parent_and_linked_ancestor(
    tree: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = tree
    with pytest.raises(PathEscapeError), open_dir_nofollow(Path("relative/dir")):
        pass
    with pytest.raises(PathEscapeError), open_dir_nofollow(root / "sub" / ".." / "sub"):
        pass
    link = tmp_path / "root-link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(OSError), open_dir_nofollow(link.resolve().parent / "root-link" / "sub"):
        pass
    with open_dir_nofollow((root / "sub").resolve()) as fd:
        assert _read(fd, "file.txt") == "inside"


def test_open_regular_at_refuses_links_hardlinks_and_special_files(
    tree: tuple[Path, Path],
) -> None:
    root, outside = tree
    sub = root / "sub"
    (sub / "link.txt").symlink_to(outside / "secret.txt")
    os.link(outside / "secret.txt", sub / "hard.txt")
    os.mkfifo(sub / "pipe")
    (sub / "dir").mkdir()
    with open_dir_beneath(root, ["sub"]) as fd:
        with pytest.raises(OSError) as info:
            open_regular_at(fd, "link.txt")
        assert info.value.errno == errno.ELOOP
        for name in ("hard.txt", "pipe", "dir"):
            with pytest.raises(NotRegularFileError):
                open_regular_at(fd, name)
        with pytest.raises(PathEscapeError):
            open_regular_at(fd, "../outside/secret.txt")


def test_create_new_at_never_overwrites_or_follows(tree: tuple[Path, Path]) -> None:
    root, outside = tree
    sub = root / "sub"
    (sub / "dangling").symlink_to(outside / "created-through-link.txt")
    with open_dir_beneath(root, ["sub"]) as fd:
        for name in ("file.txt", "dangling"):
            with pytest.raises(FileExistsError):
                create_new_at(fd, name)
        new_fd = create_new_at(fd, "fresh.txt", 0o600)
        os.close(new_fd)
    assert (sub / "fresh.txt").stat().st_mode & 0o777 == 0o600
    assert (sub / "file.txt").read_text() == "inside"
    assert not (outside / "created-through-link.txt").exists()


# ---- registry: switched sites stay on the primitive -----------------------


@pytest.mark.parametrize("module", MIGRATED_MODULES)
def test_migrated_modules_use_fs_safety_not_hand_rolled_checks(module: str) -> None:
    source = (REPO_ROOT / module).read_text(encoding="utf-8")
    assert "fs_safety" in source, f"{module} must route containment through fs_safety"
    for pattern in _HAND_ROLLED:
        assert not pattern.search(source), f"{module} reintroduces {pattern.pattern!r}"


def test_fs_safety_public_surface_is_stable() -> None:
    for name in (
        "relative_parts",
        "resolve_within",
        "open_dir_beneath",
        "open_dir_nofollow",
        "open_regular_at",
        "create_new_at",
        "PathEscapeError",
        "NotRegularFileError",
    ):
        assert hasattr(fs_safety, name)
