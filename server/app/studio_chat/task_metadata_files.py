"""Descriptor-anchored, bounded reads of untrusted local task metadata.

Directory walks and file opens go through ``server.app.fs_safety``
(SECURITY-PATH-002); this module keeps only the metadata soft-fail policy.
"""

import json
import os
import stat
from typing import Any

from server.app.fs_safety import NotRegularFileError, open_regular_at


def read_json(parent: int, name: str, *, strict: bool = False) -> dict[str, Any]:
    if not stat.S_ISREG(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
        if strict:
            raise ValueError("task metadata is not a regular file")
        return {}
    try:
        descriptor = open_regular_at(parent, name)
    except NotRegularFileError:
        if strict:
            raise ValueError("task metadata changed file type or has hard links") from None
        return {}
    try:
        data = os.read(descriptor, 65537)
    finally:
        os.close(descriptor)
    if len(data) > 65536:
        if strict:
            raise ValueError("task metadata exceeds read limit")
        return {}
    try:
        value = json.loads(data)
    except RecursionError as exc:
        if strict:
            raise ValueError("task metadata is too deeply nested") from exc
        return {}  # One corrupt file must not suppress other tasks or startup.
    if strict and not isinstance(value, dict):
        raise ValueError("task metadata is not an object")
    return value if isinstance(value, dict) else {}
