"""Content-addressed materialization cache shared by the Host and the Worker.

Materials-and-runs design §6.2: a code node never fetches a material itself
(the sandbox denies network, EXEC-CODE-003); the dispatching parent
materializes it into a local cache directory that is statically allow-read
in the sandbox (MATERIAL-ACCESS-001). Both execution sides share the exact
same cache rules via this module:

- layout: ``{cache_root}/{address[:2]}/{address}`` where ``address`` is
  the material's content hash (falling back to its id) — the address
  itself is the file name, so content addressing dedups naturally and
  the original filename never shapes the cache (it stays available in
  the runtime material block for display/identity use);
- a hit is returned as-is (mtime refreshed for the LRU);
- a miss streams the bytes into a unique sibling temp file and atomically
  renames it into place, so concurrent materializers (processes or threads)
  never observe a partial file;
- capacity is a simple oldest-mtime-first eviction towards a byte budget
  (v1); eviction failures only warn, they never block materialization.
  The file a ``materialize_stream`` call just wrote is pinned for that
  call's eviction pass, so a single over-budget material survives its own
  eviction (temporarily exceeding the budget) instead of being unlinked
  before its path is returned.

Stdlib only (``shared`` house rule): the byte source is injected as a
stream factory — the Host wraps its S3 ``ObjectStorage.open_stream``, the
Worker wraps a streaming HTTP response for the claim-time presigned URL.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO

MATERIALS_CACHE_DIRNAME = "materials_cache"

# v1 capacity discipline: one byte budget per cache root, evicted oldest
# first; the env override keeps it deploy-time tunable on both sides.
DEFAULT_CACHE_MAX_BYTES = 50 * 1024**3
_CACHE_MAX_BYTES_ENV = "AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES"

_CHUNK_BYTES = 1024 * 1024


class MaterializeError(RuntimeError):
    """Materialization failed; the message is node-facing (readable)."""


def cache_max_bytes() -> int:
    """The cache byte budget: env override or the 50 GiB default."""
    raw = os.environ.get(_CACHE_MAX_BYTES_ENV, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = -1
        if value > 0:
            return value
    return DEFAULT_CACHE_MAX_BYTES


def cache_file_path(cache_root: Path, address: str) -> Path:
    """The deterministic cache location for one addressed material."""
    address = str(address).strip()
    if not address:
        raise MaterializeError("material cache address is empty")
    return Path(cache_root) / address[:2] / address


def materialize_stream(
    cache_root: Path,
    address: str,
    stream_factory: Callable[[], BinaryIO],
    *,
    expected_sha256: str = "",
    expected_size: int | None = None,
    max_bytes: int | None = None,
    log: Callable[[str], None] = print,
) -> Path:
    """Return the local cache path for *address*, downloading on a miss.

    ``stream_factory`` is only called on a cache miss. The download lands in
    a unique temp file inside the target directory and is ``os.replace``d
    into place, so concurrent materializers never see partial content (the
    last rename wins; all winners hold identical bytes under content
    addressing). A declared sha256/size is verified before the rename — a
    mismatch raises ``MaterializeError`` and nothing is cached.

    The post-write eviction pass pins the returned path, so the file handed
    back to the caller always exists — even when it alone exceeds the byte
    budget (the budget is a target, not a hard ceiling for one material).
    """
    final = cache_file_path(cache_root, address)
    if final.is_file():
        # Refresh the LRU clock; a vanished file between the check and the
        # touch just skips the refresh.
        with contextlib.suppress(OSError):
            os.utime(final)
        return final
    final.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    written = 0
    tmp_path = final.parent / f".{final.name}.{os.getpid()}.{uuid.uuid4().hex}.part"
    try:
        stream = stream_factory()
        try:
            with tmp_path.open("wb") as handle:
                while chunk := stream.read(_CHUNK_BYTES):
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
        finally:
            getattr(stream, "close", lambda: None)()
        if expected_size is not None and written != expected_size:
            raise MaterializeError(
                f"material download size {written} does not match the recorded size {expected_size}"
            )
        if expected_sha256 and digest.hexdigest() != expected_sha256:
            raise MaterializeError(
                "material download sha256 does not match the recorded "
                f"content hash {expected_sha256}"
            )
        # Another materializer may have won the race while we downloaded:
        # re-check, then rename (atomic, last writer wins with same bytes).
        if not final.is_file():
            os.replace(tmp_path, final)
        else:
            tmp_path.unlink(missing_ok=True)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    budget = cache_max_bytes() if max_bytes is None else max_bytes
    evict_to_capacity(Path(cache_root), budget, pin={final}, log=log)
    return final


def evict_to_capacity(
    cache_root: Path,
    max_bytes: int,
    *,
    pin: Iterable[Path] | None = None,
    log: Callable[[str], None] = print,
) -> None:
    """Evict oldest-mtime cache files until the root fits *max_bytes*.

    Eviction never affects correctness (the next materialization
    re-downloads) and never blocks: any filesystem error downgrades to a
    warning. Paths in *pin* are never unlinked, even when they keep the
    root over budget — a single pinned over-budget material temporarily
    exceeds the budget rather than being deleted out from under its
    consumer. Other files currently served to a sandbox may still
    disappear under it — the mtime refresh on every hit keeps
    recently-used entries young, which is the v1 mitigation.
    """
    root = Path(cache_root)
    pinned = {Path(p) for p in pin or ()}
    try:
        entries: list[tuple[float, int, Path]] = []
        total = 0
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                path = Path(dirpath) / name
                with contextlib.suppress(OSError):
                    stat = path.stat()
                    entries.append((stat.st_mtime, stat.st_size, path))
                    total += stat.st_size
        if total <= max_bytes:
            return
        for _mtime, size, path in sorted(e for e in entries if e[2] not in pinned):
            if total <= max_bytes:
                break
            try:
                path.unlink()
                total -= size
            except OSError as exc:
                log(f"materials cache eviction skipped {path}: {exc}")
        # Drop the address dirs left empty by eviction (best effort).
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            if not dirnames and not filenames and Path(dirpath) != root:
                with contextlib.suppress(OSError):
                    Path(dirpath).rmdir()
    except OSError as exc:
        log(f"materials cache eviction failed for {root}: {exc}")
