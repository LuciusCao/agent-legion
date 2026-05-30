from unittest.mock import patch

import pytest
import requests

from server.app.pipeline.download import download_video


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, headers: dict | None = None):
        self._content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_download_video_writes_chunks(tmp_path):
    output = tmp_path / "video.mp4"
    data = b"abcd" * 300  # > 1 MiB to trigger multiple chunks

    with patch(
        "server.app.pipeline.download.requests.get", return_value=FakeResponse(data)
    ) as mock_get:
        download_video("https://example.com/video.mp4", output)

    mock_get.assert_called_once_with("https://example.com/video.mp4", stream=True, timeout=120)
    assert output.read_bytes() == data


def test_download_video_skips_when_file_exists_and_non_empty(tmp_path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"existing")

    with patch("server.app.pipeline.download.requests.get") as mock_get:
        download_video("https://example.com/video.mp4", output)

    mock_get.assert_not_called()
    assert output.read_bytes() == b"existing"


def test_download_video_re_downloads_when_file_is_empty(tmp_path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"")
    data = b"new content"

    with patch("server.app.pipeline.download.requests.get", return_value=FakeResponse(data)):
        download_video("https://example.com/video.mp4", output)

    assert output.read_bytes() == data


def test_download_video_raises_on_http_error(tmp_path):
    output = tmp_path / "video.mp4"

    with (
        patch(
            "server.app.pipeline.download.requests.get",
            return_value=FakeResponse(b"", status_code=404),
        ),
        pytest.raises(requests.HTTPError),
    ):
        download_video("https://example.com/missing.mp4", output)


def test_download_video_rejects_non_video_mime_type(tmp_path):
    output = tmp_path / "video.mp4"

    with (
        patch(
            "server.app.pipeline.download.requests.get",
            return_value=FakeResponse(b"<html></html>", headers={"content-type": "text/html"}),
        ),
        pytest.raises(ValueError, match="Expected video content"),
    ):
        download_video("https://example.com/trap.mp4", output)

    assert not output.exists()


def test_download_video_cleans_up_partial_file_on_error(tmp_path):
    output = tmp_path / "video.mp4"

    class BrokenResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = {"content-type": "video/mp4"}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"partial"
            raise RuntimeError("connection reset")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with (
        patch(
            "server.app.pipeline.download.requests.get",
            return_value=BrokenResponse(),
        ),
        pytest.raises(RuntimeError, match="connection reset"),
    ):
        download_video("https://example.com/broken.mp4", output)

    assert not output.exists()
