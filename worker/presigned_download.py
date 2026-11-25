"""Presigned material downloads with secret-free streaming failures."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO, cast

import requests
import urllib3.exceptions

from shared.material_cache import MaterializeError, materialize_stream
from worker.artifact.download import describe_transfer_error

_DOWNLOAD_TIMEOUT_SECONDS = 120


def _download_error(label: str, exc: BaseException) -> MaterializeError:
    return MaterializeError(f"{label} download failed: {describe_transfer_error(exc)}")


def open_presigned_download(url: str, *, if_match: str = "") -> BinaryIO:
    headers = {"If-Match": f'"{if_match}"'} if if_match else {}
    try:
        response = requests.get(
            url, stream=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS, headers=headers
        )
    except requests.RequestException as exc:
        raise _download_error("material", exc) from None
    if response.status_code == 412:
        response.close()
        raise MaterializeError(
            "material bytes changed after the server's completion check "
            "(ETag mismatch); an operator must repair the row with "
            "scripts/request_material_reupload.py, then re-upload the material"
        )
    if response.status_code != 200:
        response.close()
        raise MaterializeError(f"material download failed with HTTP {response.status_code}")
    return cast(BinaryIO, response.raw)


def materialize_presigned_stream(
    cache_root: Path,
    address: str,
    stream_factory: Callable[[], BinaryIO],
    *,
    label: str,
    expected_sha256: str = "",
    expected_size: int | None = None,
    pin: Iterable[Path] | None = None,
) -> Path:
    """Materialize without allowing signed URLs into exception chains or messages."""
    try:
        return materialize_stream(
            cache_root,
            address,
            stream_factory,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            pin=pin,
        )
    except MaterializeError:
        raise
    except (requests.RequestException, urllib3.exceptions.HTTPError, OSError) as exc:
        raise _download_error(label, exc) from None
