"""Raw (binary) artifact location: local job_dir file or object-store stream.

Split from services/job_artifacts.py for the architecture file budget; the
text read path stays in JobArtifactService.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_errors import NotFoundError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawArtifact:
    """A binary artifact handle: local file path or an object-store stream.

    Local files are served by FileResponse (native Range support for media
    seeking); stream-backed artifacts come from object storage and stream
    whole-body on first response.
    """

    name: str
    path: Path | None = None
    stream: BinaryIO | None = None
    size_bytes: int | None = None


def open_raw_artifact(
    artifact_path: Path,
    object_store: JobArtifactObjectStore | None,
    job_id: str,
    artifact_name: str,
) -> RawArtifact:
    """Locate a binary-servable artifact: local job_dir file first, then the
    object-store stream (mirrors the text read()'s ordering).

    Storage failures raise NotFoundError, matching read()'s degrade-to-404
    semantics; the caller is responsible for consuming (and closing) the
    returned handle.
    """
    if artifact_path.exists() and artifact_path.is_file():
        return RawArtifact(name=artifact_name, path=artifact_path)
    store = object_store
    if store is not None and store.enabled:
        row = store.lookup(job_id, artifact_name)
        if row is not None:
            try:
                stream = store.open_stream(row)
            except Exception:
                logger.warning(
                    "failed to open raw artifact %s of job %s from object storage",
                    artifact_name,
                    job_id,
                    exc_info=True,
                )
                raise NotFoundError("Artifact not found") from None
            size = row.get("size_bytes")
            return RawArtifact(
                name=artifact_name,
                stream=stream,
                size_bytes=int(size) if isinstance(size, int) else None,
            )
    raise NotFoundError("Artifact not found")
