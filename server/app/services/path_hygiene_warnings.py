"""Process-wide warning policy for legacy absolute database paths."""

from __future__ import annotations

import logging
import threading
import warnings

logger = logging.getLogger("server.app.services.path_hygiene")

_LEGACY_MESSAGE = "Legacy absolute path stored; resolving relative to data_dir instead"
_LEGACY_WARN_DEDUPE_CAP = 10_000
_legacy_absolute_seen: set[str] = set()
_legacy_absolute_lock = threading.Lock()


def warn_legacy_absolute(
    stored_path: str = "", *, dedupe_cap: int = _LEGACY_WARN_DEDUPE_CAP
) -> None:
    """Log and warn once per stored path, with bounded dedupe memory."""
    with _legacy_absolute_lock:
        if stored_path and stored_path in _legacy_absolute_seen:
            return
        if stored_path and len(_legacy_absolute_seen) < dedupe_cap:
            _legacy_absolute_seen.add(stored_path)
    logger.warning(_LEGACY_MESSAGE)
    warnings.warn(_LEGACY_MESSAGE, DeprecationWarning, stacklevel=4)


def reset_legacy_absolute_dedupe() -> None:
    """Clear the warn-dedupe set (test isolation between per-tmp-path cases)."""
    with _legacy_absolute_lock:
        _legacy_absolute_seen.clear()
