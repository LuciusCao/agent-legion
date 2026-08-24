"""shared/material_cache.py：内容寻址物化缓存的命中/未命中/原子性/并发/淘汰。"""

from __future__ import annotations

import hashlib
import io
import os
import threading
import time
from pathlib import Path

import pytest

from shared.material_cache import (
    DEFAULT_CACHE_MAX_BYTES,
    MaterializeError,
    cache_file_path,
    cache_max_bytes,
    evict_to_capacity,
    materialize_stream,
)

pytestmark = pytest.mark.no_db

PAYLOAD = b"material-bytes" * 100
HASH = hashlib.sha256(PAYLOAD).hexdigest()


def _stream(payload: bytes = PAYLOAD) -> io.BytesIO:
    return io.BytesIO(payload)


def test_miss_downloads_into_content_addressed_path(tmp_path: Path) -> None:
    path = materialize_stream(
        tmp_path, HASH, _stream, expected_sha256=HASH, expected_size=len(PAYLOAD)
    )

    assert path == tmp_path / HASH[:2] / HASH
    assert path.read_bytes() == PAYLOAD


def test_hit_skips_the_stream_and_refreshes_mtime(tmp_path: Path) -> None:
    path = materialize_stream(tmp_path, HASH, _stream, expected_sha256=HASH)
    old = time.time() - 3600
    os.utime(path, (old, old))

    def _forbidden() -> io.BytesIO:
        raise AssertionError("cache hit must not open the stream")

    again = materialize_stream(tmp_path, HASH, _forbidden, expected_sha256=HASH)

    assert again == path
    assert time.time() - path.stat().st_mtime < 60


def test_hash_mismatch_raises_and_caches_nothing(tmp_path: Path) -> None:
    with pytest.raises(MaterializeError, match="sha256"):
        materialize_stream(tmp_path, HASH, lambda: _stream(b"tampered"), expected_sha256=HASH)

    assert not (tmp_path / HASH[:2] / HASH).exists()
    # 临时文件不残留。
    assert list(tmp_path.rglob("*.part")) == []


def test_size_mismatch_raises(tmp_path: Path) -> None:
    with pytest.raises(MaterializeError, match="size"):
        materialize_stream(tmp_path, HASH, _stream, expected_size=len(PAYLOAD) + 1)


def test_empty_address_rejected(tmp_path: Path) -> None:
    with pytest.raises(MaterializeError, match="address"):
        cache_file_path(tmp_path, "  ")


def test_concurrent_materializers_converge_on_one_file(tmp_path: Path) -> None:
    downloads = 0
    lock = threading.Lock()

    def _slow_stream() -> io.BytesIO:
        nonlocal downloads
        with lock:
            downloads += 1
        # 拉长下载窗口，让并发者真实撞上在途下载。
        time.sleep(0.05)
        return _stream()

    results: list[Path] = []
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            results.append(materialize_stream(tmp_path, HASH, _slow_stream, expected_sha256=HASH))
        except BaseException as exc:  # noqa: BLE001 - 汇聚后统一断言
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    assert len(results) == 8
    final = tmp_path / HASH[:2] / HASH
    assert all(path == final for path in results)
    assert final.read_bytes() == PAYLOAD
    assert list(tmp_path.rglob("*.part")) == []


def test_eviction_removes_oldest_first(tmp_path: Path) -> None:
    entries = []
    for index in range(4):
        payload = f"payload-{index}".encode() * 10
        digest = hashlib.sha256(payload).hexdigest()
        path = materialize_stream(tmp_path, digest, lambda p=payload: _stream(p))
        # 手动拉开 mtime：index 越小越旧。
        mtime = time.time() - (100 - index)
        os.utime(path, (mtime, mtime))
        entries.append((path, len(payload)))

    total = sum(size for _, size in entries)
    # 容量只够留下最新的两个。
    evict_to_capacity(tmp_path, entries[2][1] + entries[3][1])

    assert not entries[0][0].exists()
    assert not entries[1][0].exists()
    assert entries[2][0].exists()
    assert entries[3][0].exists()
    assert total > entries[2][1] + entries[3][1]


def test_materialize_pins_the_fresh_file_against_its_own_eviction(tmp_path: Path) -> None:
    # 单个材料大于容量上限时，物化后回收不得删掉刚写入的文件。
    path = materialize_stream(tmp_path, HASH, _stream, expected_sha256=HASH, max_bytes=1)

    assert path.exists()
    assert path.read_bytes() == PAYLOAD
    assert len(PAYLOAD) > 1


def test_materialize_eviction_keeps_pin_but_evicts_older_entries(tmp_path: Path) -> None:
    old_payload = b"old" * 10
    old_digest = hashlib.sha256(old_payload).hexdigest()
    old_path = materialize_stream(tmp_path, old_digest, lambda: _stream(old_payload))
    mtime = time.time() - 3600
    os.utime(old_path, (mtime, mtime))

    # 容量装不下两个文件：旧文件被回收，新文件（pin）即使超预算也保留。
    new_path = materialize_stream(
        tmp_path, HASH, _stream, expected_sha256=HASH, max_bytes=len(PAYLOAD)
    )

    assert not old_path.exists()
    assert new_path.exists()
    assert new_path.read_bytes() == PAYLOAD


def test_eviction_never_unlinks_pinned_paths(tmp_path: Path) -> None:
    first = materialize_stream(tmp_path, HASH, _stream, expected_sha256=HASH)
    second_payload = b"second" * 50
    second_digest = hashlib.sha256(second_payload).hexdigest()
    second = materialize_stream(tmp_path, second_digest, lambda: _stream(second_payload))
    mtime = time.time() - 3600
    os.utime(first, (mtime, mtime))

    # pin 住最旧的文件：它必须存活，即使总量因此留在预算之上。
    evict_to_capacity(tmp_path, 1, pin={first})

    assert first.exists()
    assert not second.exists()


def test_eviction_removes_bundle_tree_atomically(tmp_path: Path) -> None:
    # 一个 bundle 目录树 entry（shard/address/…）+ 一个更新的单文件。
    tree = tmp_path / "ab" / ("ab" + "0" * 62)
    (tree / "sub").mkdir(parents=True)
    (tree / "a.txt").write_bytes(b"a" * 100)
    (tree / "sub" / "b.txt").write_bytes(b"b" * 100)
    old = time.time() - 3600
    for file in (tree / "a.txt", tree / "sub" / "b.txt"):
        os.utime(file, (old, old))
    newer = materialize_stream(tmp_path, HASH, _stream, expected_sha256=HASH)

    # 容量只够留单文件：整棵树被原子删掉，不允许残缺目录残留
    # （assemble_bundle_tree 把「目录存在」视为完整，#156）。
    evict_to_capacity(tmp_path, len(PAYLOAD))

    assert not tree.exists()
    assert not (tmp_path / "ab").exists()  # 空 shard 目录一并清理
    assert newer.exists()


def test_eviction_keeps_pinned_tree(tmp_path: Path) -> None:
    tree = tmp_path / "cd" / ("cd" + "0" * 62)
    tree.mkdir(parents=True)
    (tree / "a.txt").write_bytes(b"a" * 100)
    old = time.time() - 3600
    os.utime(tree / "a.txt", (old, old))

    evict_to_capacity(tmp_path, 1, pin={tree})

    assert (tree / "a.txt").read_bytes() == b"a" * 100


def test_eviction_uses_newest_file_mtime_as_tree_clock(tmp_path: Path) -> None:
    # 树内任一文件被命中刷新过（assemble 的 utime 循环），整棵树保持年轻。
    tree = tmp_path / "ef" / ("ef" + "0" * 62)
    tree.mkdir(parents=True)
    (tree / "a.txt").write_bytes(b"a" * 100)
    (tree / "b.txt").write_bytes(b"b" * 100)
    os.utime(tree / "a.txt", (time.time() - 3600,) * 2)  # b.txt 保持新 mtime
    old_payload = b"old" * 100
    old_digest = hashlib.sha256(old_payload).hexdigest()
    old_file = materialize_stream(tmp_path, old_digest, lambda: _stream(old_payload))
    os.utime(old_file, (time.time() - 7200,) * 2)  # 严格老于树内任何文件

    # 容量只够留树：老文件被回收；若树按文件粒度淘汰，a.txt 会先被删成残缺树。
    evict_to_capacity(tmp_path, 200)

    assert not old_file.exists()
    assert (tree / "a.txt").exists()
    assert (tree / "b.txt").exists()


def test_materialize_extra_pin_protects_sibling_entry(tmp_path: Path) -> None:
    first = materialize_stream(tmp_path, HASH, _stream, expected_sha256=HASH)
    old = time.time() - 3600
    os.utime(first, (old, old))
    second_payload = b"second" * 50
    second_digest = hashlib.sha256(second_payload).hexdigest()

    # 容量装不下两个文件，但 extra pin 保护先物化的成员（bundle 场景）。
    second = materialize_stream(
        tmp_path, second_digest, lambda: _stream(second_payload), max_bytes=1, pin={first}
    )

    assert first.exists()
    assert second.exists()


def test_eviction_failure_only_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = materialize_stream(tmp_path, HASH, _stream)
    warnings: list[str] = []

    def _failing_unlink(self: Path, missing_ok: bool = False) -> None:  # noqa: ARG001
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "unlink", _failing_unlink)
    evict_to_capacity(tmp_path, 1, log=warnings.append)

    assert warnings, "eviction failure must surface as a warning"
    assert path.exists()


def test_cache_max_bytes_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cache_max_bytes() == DEFAULT_CACHE_MAX_BYTES
    monkeypatch.setenv("AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES", "1024")
    assert cache_max_bytes() == 1024
    monkeypatch.setenv("AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES", "not-a-number")
    assert cache_max_bytes() == DEFAULT_CACHE_MAX_BYTES
