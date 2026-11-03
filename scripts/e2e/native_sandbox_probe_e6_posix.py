"""POSIX raw-evidence readers for the candidate-only E6 payload."""

from __future__ import annotations

import os
from typing import Any


def stdio_evidence() -> dict[str, list[int]]:
    directory_stat = os.stat("/proc/self/fd")
    identity = (directory_stat.st_dev, directory_stat.st_ino)
    with os.scandir("/proc/self/fd") as entries:
        observed = set()
        enumeration_fds = set()
        for entry in entries:
            fd = int(entry.name)
            observed.add(fd)
            stat = entry.stat(follow_symlinks=True)
            if (stat.st_dev, stat.st_ino) == identity:
                enumeration_fds.add(fd)
    return {"observed": sorted(observed), "enumeration_fds": sorted(enumeration_fds)}


def host_mounts_hidden(config: dict[str, Any], mountinfo: str) -> bool:
    return config["canary_path"] not in mountinfo and all(
        field == config["work_path"] or config["state_dir"] not in field
        for line in mountinfo.splitlines()
        for field in line.split()
    )
