"""Descriptor-anchored, bounded reads of untrusted local task metadata."""

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def directory(path: Path) -> Iterator[int]:
    """Open every ancestor without following links; pin subsequent traversal."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("task metadata path must be absolute")
    descriptor = os.open(path.anchor, DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def read_json(parent: int, name: str) -> dict[str, Any]:
    if not stat.S_ISREG(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
        return {}
    descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return {}
        data = os.read(descriptor, 65537)
    finally:
        os.close(descriptor)
    if len(data) > 65536:
        return {}
    value = json.loads(data)
    return value if isinstance(value, dict) else {}
