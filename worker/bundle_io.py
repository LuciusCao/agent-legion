"""Shared bundle/artifact IO for Worker execution preparation.

The tar-bundle extraction lives here; the input-artifact download channel
(``download_input_artifacts`` / ``sha256_file``) split to
``worker.artifact.inputs`` for the file-size budget and is re-exported so
existing import paths keep working.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, cast

from worker.artifact.inputs import download_input_artifacts, sha256_file

__all__ = [
    "download_input_artifacts",
    "safe_extract",
    "safe_extract_tree",
    "sha256_file",
]


def safe_extract_tree(archive: Path, destination: Path) -> None:
    """Extract a tar.gz bundle, rejecting absolute/parent/link members."""
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.islnk() or member.issym():
                raise ValueError(f"unsafe Agent bundle member: {member.name!r}")
        tar.extractall(destination, filter="data")


def safe_extract(archive: Path, destination: Path) -> dict[str, Any]:
    """Agent bundles carry a manifest.json; code bundles do not."""
    safe_extract_tree(archive, destination)
    return cast(dict[str, Any], json.loads((destination / "manifest.json").read_text()))
