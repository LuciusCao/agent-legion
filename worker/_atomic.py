"""Package-internal atomic file write: same-directory temp file + fsync + replace."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path


def atomic_write(path: Path, content: str, *, mode: int | None = None) -> None:
    """Write ``content`` to ``path`` so concurrent readers and crashes never
    see a half-written file; ``mode`` (e.g. 0o600) is applied before replace."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f"{path.stem}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        # #204 broad-except audit: staging-file cleanup guard, not a
        # swallow — the bare raise re-raises the original verbatim. The
        # width is deliberate: cancellation/GeneratorExit during a partial
        # write must also remove the .part temp file.
        with suppress(OSError):
            os.unlink(temporary)
        raise
