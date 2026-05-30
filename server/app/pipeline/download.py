from pathlib import Path

import requests

from server.app.security import validate_download_url


def download_video(url: str, output_path: Path) -> None:
    validate_download_url(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 0:
        return
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if content_type and not (
            content_type.startswith("video/") or content_type.startswith("application/octet-stream")
        ):
            raise ValueError(f"Expected video content, got {content_type}")
        try:
            with output_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        except Exception:
            if output_path.exists():
                output_path.unlink()
            raise
