"""#1097: the ``_shared`` swap checks identity, never follows links.

Decision table (checkpoint × state of ``_shared``), one parametrized case
per cell. Checkpoints: the snapshot taken under the lock, the descriptor
opened for the residue carry, the re-check right before the retiring
rename, and the residual window between that re-check and the rename
(caught by the post-move verification). Every mismatch fails closed with
``SharedMaterialWriteError``; nothing outside the tree is read, moved or
removed, and no staging/retired leftovers stay behind.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from server.app import fs_dir_identity
from server.app.services import skill_shared_swap
from server.app.services.skill_shared_store import SharedMaterialWriteError
from server.app.services.skill_shared_swap import write_shared_materials

pytestmark = pytest.mark.no_db

_PYC = b"\xcb\r\r\n\x00\x00\x00\x00bytecode"
_MAP = json.dumps({"version": 1, "materials": []})
_PAYLOAD = [("map.json", _MAP), ("scripts/common.py", "X = 2\n")]


def _tree(root: Path, marker: str) -> Path:
    (root / "scripts" / "__pycache__").mkdir(parents=True)
    (root / "map.json").write_text(_MAP, encoding="utf-8")
    (root / "scripts" / "common.py").write_text(f"{marker}\n", encoding="utf-8")
    (root / "scripts" / "__pycache__" / f"{marker}.cpython-312.pyc").write_bytes(_PYC)
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    return {
        "ws": ws,
        "shared": ws / "_shared",
        "outside": _tree(tmp_path / "outside", "outside"),
        "other": _tree(tmp_path / "other", "other"),
        "moved": tmp_path / "moved-away",
    }


def _replace(layout: dict[str, Path], kind: str) -> None:
    """Out-of-band replacement of the live ``_shared`` (the real dir is
    moved aside so the test can prove it was never touched)."""
    os.rename(layout["shared"], layout["moved"])
    if kind == "symlink":
        layout["shared"].symlink_to(layout["outside"], target_is_directory=True)
    else:
        os.rename(layout["other"], layout["shared"])


def _no_leftovers(ws: Path) -> None:
    assert not [p.name for p in ws.iterdir() if p.name.startswith("_shared.")]


def test_normal_directory_swaps_and_carries_residue(layout: dict[str, Path]) -> None:
    _tree(layout["shared"], "live")
    write_shared_materials(layout["shared"], _PAYLOAD, layout["ws"])
    shared = layout["shared"]
    assert (shared / "scripts" / "common.py").read_text(encoding="utf-8") == "X = 2\n"
    assert (shared / "scripts" / "__pycache__" / "live.cpython-312.pyc").read_bytes() == _PYC
    _no_leftovers(layout["ws"])


def test_absent_directory_is_a_first_write(layout: dict[str, Path]) -> None:
    write_shared_materials(layout["shared"], _PAYLOAD, layout["ws"])
    assert sorted(_snapshot(layout["shared"])) == ["map.json", "scripts/common.py"]
    _no_leftovers(layout["ws"])


def test_symlinked_workspace_dir_is_a_trusted_anchor(
    layout: dict[str, Path], tmp_path: Path
) -> None:
    """The parent is opened once and followed (like fs_safety's root):
    a deployment that symlinks the workspace skill dir keeps working."""
    real = tmp_path / "real-ws"
    real.mkdir()
    _tree(real / "_shared", "live")
    link = tmp_path / "linked-ws"
    link.symlink_to(real, target_is_directory=True)
    write_shared_materials(link / "_shared", _PAYLOAD, tmp_path)
    assert (real / "_shared" / "scripts" / "common.py").read_text(encoding="utf-8") == "X = 2\n"
    _no_leftovers(real)


@pytest.mark.parametrize("kind", ["symlink", "file", "dangling-symlink"])
def test_live_name_that_is_not_a_real_directory_fails_closed(
    layout: dict[str, Path], kind: str
) -> None:
    shared, before = layout["shared"], _snapshot(layout["outside"])
    if kind == "symlink":
        shared.symlink_to(layout["outside"], target_is_directory=True)
    elif kind == "dangling-symlink":
        shared.symlink_to(layout["ws"] / "missing", target_is_directory=True)
    else:
        shared.write_text("not a dir", encoding="utf-8")
    with pytest.raises(SharedMaterialWriteError, match="not a real directory"):
        write_shared_materials(shared, _PAYLOAD, layout["ws"])
    assert os.path.lexists(shared) and (shared.is_symlink() or kind == "file")
    assert _snapshot(layout["outside"]) == before
    _no_leftovers(layout["ws"])


def _swap_before(
    monkeypatch: pytest.MonkeyPatch, module: object, name: str, action: Callable[[], None]
) -> None:
    real = getattr(module, name)

    def wrapped(*args, **kwargs):
        action()
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapped)


def _swap_after(
    monkeypatch: pytest.MonkeyPatch, module: object, name: str, action: Callable[[], None]
) -> None:
    real = getattr(module, name)

    def wrapped(*args, **kwargs):
        result = real(*args, **kwargs)
        action()
        return result

    monkeypatch.setattr(module, name, wrapped)


# checkpoint → how the replacement is injected (module, function, before/after)
_CHECKPOINTS = {
    # Between the lstat snapshot and the O_NOFOLLOW open for the carry.
    "before-carry-open": (skill_shared_swap, "open_dir_identity_at", _swap_before),
    # While the carry walks the pinned descriptor: reads stay on the
    # snapshot, the re-check before the rename refuses the move.
    "during-carry": (skill_shared_swap, "carry_build_residue", _swap_before),
    # Residual window: after the pre-rename re-check, before the rename —
    # the post-move verification catches it and moves the entry back.
    "after-recheck": (fs_dir_identity, "require_dir_identity_at", _swap_after),
}


@pytest.mark.parametrize("kind", ["symlink", "other-directory"])
@pytest.mark.parametrize("checkpoint", sorted(_CHECKPOINTS))
def test_replacement_after_the_snapshot_fails_closed(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch, checkpoint: str, kind: str
) -> None:
    _tree(layout["shared"], "live")
    original = _snapshot(layout["shared"])
    outside_before = _snapshot(layout["outside"])
    other_before = _snapshot(layout["other"])
    module, name, inject = _CHECKPOINTS[checkpoint]
    inject(monkeypatch, module, name, lambda: _replace(layout, kind))

    # A symlink trips O_NOFOLLOW / the lstat type check, another directory
    # the (st_dev, st_ino) comparison — both are the same fail-closed error.
    with pytest.raises(SharedMaterialWriteError, match="shared materials write failed"):
        write_shared_materials(layout["shared"], _PAYLOAD, layout["ws"])

    shared = layout["shared"]
    # The replacement is back (or still) in place, untouched; nothing was
    # promoted over it and nothing outside the tree changed.
    if kind == "symlink":
        assert shared.is_symlink()
        assert _snapshot(layout["outside"]) == outside_before
    else:
        assert not shared.is_symlink()
        assert _snapshot(shared) == other_before
    assert _snapshot(layout["outside"]) == outside_before
    # The real previous dir (moved aside out of band) is intact.
    assert _snapshot(layout["moved"]) == original
    _no_leftovers(layout["ws"])


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "symlinked-pycache-dir"])
def test_residue_carry_copies_only_single_link_regular_files(
    layout: dict[str, Path], kind: str
) -> None:
    shared = _tree(layout["shared"], "live")
    secret = layout["outside"] / "scripts" / "__pycache__" / "outside.cpython-312.pyc"
    pycache = shared / "scripts" / "__pycache__"
    if kind == "symlink":
        (pycache / "bad.pyc").symlink_to(secret)
    elif kind == "hardlink":
        os.link(secret, pycache / "bad.pyc")
    elif kind == "fifo":
        os.mkfifo(pycache / "bad.pyc")
    else:
        os.makedirs(shared / "refs")
        (shared / "refs" / "__pycache__").symlink_to(secret.parent, target_is_directory=True)
    write_shared_materials(shared, [*_PAYLOAD, ("refs/a.md", "a\n")], layout["ws"])
    assert (pycache / "live.cpython-312.pyc").read_bytes() == _PYC
    assert not os.path.lexists(pycache / "bad.pyc")
    assert not os.path.lexists(shared / "refs" / "__pycache__")
    assert secret.read_bytes() == _PYC
    _no_leftovers(layout["ws"])


@pytest.mark.parametrize("restore_fails", [False, True])
def test_promote_failure_restores_or_preserves_the_verified_previous_dir(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch, restore_fails: bool
) -> None:
    """The fd-relative promote/restore keep the codex R4 guard: a failed
    promote restores the retired dir; if the restore fails too, the
    retired dir (proven to be the previous live dir) stays on disk."""
    _tree(layout["shared"], "live")
    original = _snapshot(layout["shared"])
    real_rename = skill_shared_swap.rename_at

    def flaky_rename(parent: int, source: str, target: str) -> None:
        if ".tmp-" in source or (restore_fails and ".old-" in source):
            raise OSError("rename refused")
        real_rename(parent, source, target)

    monkeypatch.setattr(skill_shared_swap, "rename_at", flaky_rename)
    with pytest.raises(SharedMaterialWriteError) as caught:
        write_shared_materials(layout["shared"], _PAYLOAD, layout["ws"])
    leftovers = [p for p in layout["ws"].iterdir() if p.name.startswith("_shared.")]
    if restore_fails:
        [retired] = leftovers
        assert retired.name.startswith("_shared.old-")
        assert str(retired) in str(caught.value)
        assert _snapshot(retired) == original
        assert not os.path.lexists(layout["shared"])
    else:
        assert leftovers == []
        assert _snapshot(layout["shared"]) == original
