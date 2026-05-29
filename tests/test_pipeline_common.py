from pathlib import Path

import pytest

from server.app.cms.knowledge import _extract_knowledge_url
from server.app.pipeline.common import get_video_id, parse_srt, resolve_video_dir


def test_parse_srt_and_video_id():
    assert get_video_id("https://cdn.example.com/videos/g02060101.mp4?x=1") == "g02060101"

    subtitles = parse_srt(
        "1\n00:00:00,000 --> 00:00:01,500\n你好\n\n2\n00:00:01,500 --> 00:00:03,000\n继续\n"
    )

    assert subtitles == [
        {"index": 1, "start": 0.0, "end": 1.5, "text": "你好"},
        {"index": 2, "start": 1.5, "end": 3.0, "text": "继续"},
    ]


def test_extract_knowledge_url_accepts_source_v2():
    payload = {
        "data": {
            "knowledge_code": "K001",
            "resource": [
                {
                    "resource_type": 1,
                    "video_data": {
                        "source_v2": "https://example.com/k001.mp4",
                    },
                }
            ],
        }
    }

    assert _extract_knowledge_url("K001", payload) == ("https://example.com/k001.mp4", "")


def test_resolve_video_dir_prefers_storage_dir():
    videos_dir = Path("/data/videos")
    assert resolve_video_dir({"id": "v1", "storage_dir": "/custom/dir"}, videos_dir) == Path(
        "/custom/dir"
    )
    assert resolve_video_dir({"id": "v1", "storage_dir": ""}, videos_dir) == Path("/data/videos/v1")
    assert resolve_video_dir({"id": "v1"}, videos_dir) == Path("/data/videos/v1")


def test_parse_time_unknown_format():
    from server.app.pipeline.common import parse_time

    with pytest.raises(ValueError, match="Unknown timestamp"):
        parse_time("invalid")


def test_parse_time_with_single_part():
    from server.app.pipeline.common import parse_time

    with pytest.raises(ValueError, match="Unknown timestamp"):
        parse_time("1")


def test_format_srt_time():
    from server.app.pipeline.common import format_srt_time

    assert format_srt_time(0) == "00:00:00,000"
    assert format_srt_time(3661.5) == "01:01:01,500"
    assert format_srt_time(0.999) == "00:00:00,999"


def test_parse_srt_skips_invalid_time_line():
    from server.app.pipeline.common import parse_srt

    result = parse_srt("1\ninvalid line\nhello\n\n2\n00:00:00,000 --> 00:00:01,000\nworld\n")
    assert len(result) == 1
    assert result[0]["text"] == "world"
