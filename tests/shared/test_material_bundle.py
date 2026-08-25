"""shared/material_bundle.py：bundle 地址与目录树组装（#156）。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from shared.material_bundle import assemble_bundle_tree, bundle_address
from shared.material_cache import MaterializeError

pytestmark = pytest.mark.no_db


def test_bundle_address_is_order_independent_and_content_sensitive() -> None:
    first = bundle_address([("hash-a", "a.txt"), ("hash-b", "sub/b.txt")])
    second = bundle_address([("hash-b", "sub/b.txt"), ("hash-a", "a.txt")])
    assert first == second
    assert first != bundle_address([("hash-a", "a.txt"), ("hash-b", "other/b.txt")])
    assert first != bundle_address([("hash-a", "a.txt")])
    assert first == hashlib.sha256(b"hash-a\ta.txt\nhash-b\tsub/b.txt").hexdigest()


def test_bundle_address_rejects_empty_manifest() -> None:
    with pytest.raises(MaterializeError, match="empty"):
        bundle_address([])


def test_bundle_address_rejects_control_characters() -> None:
    """拼接编码的单射性依赖路径不含 TAB/LF：含控制字符直接拒绝（#156）。"""
    with pytest.raises(MaterializeError, match="control"):
        bundle_address([("hash-a", "x\nhash-b\ty.txt")])
    with pytest.raises(MaterializeError, match="control"):
        bundle_address([("hash-a", "ok.txt"), ("hash-b", "tab\tname.txt")])


def _member(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_assemble_builds_tree_atomically(tmp_path: Path) -> None:
    member_a = _member(tmp_path, "ma", b"aaa")
    member_b = _member(tmp_path, "mb", b"bbb")
    cache_root = tmp_path / "cache"
    address = bundle_address([("hash-a", "a.txt"), ("hash-b", "sub/b.txt")])

    tree = assemble_bundle_tree(cache_root, address, [(member_a, "a.txt"), (member_b, "sub/b.txt")])

    assert tree == cache_root / address[:2] / address
    assert (tree / "a.txt").read_bytes() == b"aaa"
    assert (tree / "sub" / "b.txt").read_bytes() == b"bbb"
    # 硬链接共享 inode：成员缓存文件被驱逐不破坏目录树。
    assert (tree / "a.txt").stat().st_ino == member_a.stat().st_ino
    member_a.unlink()
    assert (tree / "a.txt").read_bytes() == b"aaa"


def test_assemble_returns_existing_tree_without_rebuild(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    address = bundle_address([("hash-a", "a.txt")])
    final = cache_root / address[:2] / address
    final.mkdir(parents=True)
    (final / "a.txt").write_bytes(b"existing")

    tree = assemble_bundle_tree(cache_root, address, [(_member(tmp_path, "mx", b"new"), "a.txt")])

    assert tree == final
    assert (tree / "a.txt").read_bytes() == b"existing"


def test_assemble_discards_temp_tree_on_lost_rename_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重命名竞争失败方：返回胜者的树，自己的 .part 临时树必须清理（#156）。"""
    member = _member(tmp_path, "ma", b"aaa")
    cache_root = tmp_path / "cache"
    address = bundle_address([("hash-a", "a.txt")])
    final = cache_root / address[:2] / address

    def _winner_won(src: Path, dst: Path) -> None:  # noqa: ARG001
        # 胜者先落位；我们的 os.replace 因目标是非空目录而失败。
        dst.mkdir(parents=True)
        (dst / "a.txt").write_bytes(b"winner")
        raise OSError("Directory not empty")

    monkeypatch.setattr("shared.material_bundle.os.replace", _winner_won)

    tree = assemble_bundle_tree(cache_root, address, [(member, "a.txt")])

    assert tree == final
    assert (tree / "a.txt").read_bytes() == b"winner"
    assert list(cache_root.rglob("*.part")) == []


def test_assemble_rejects_unsafe_relpath(tmp_path: Path) -> None:
    member = _member(tmp_path, "ma", b"aaa")
    with pytest.raises(MaterializeError, match="relative path"):
        assemble_bundle_tree(tmp_path / "cache", "ab" + "0" * 62, [(member, "../evil.txt")])
    with pytest.raises(MaterializeError, match="relative path"):
        assemble_bundle_tree(tmp_path / "cache", "ab" + "0" * 62, [(member, "ctrl\nname.txt")])
