from pathlib import Path

import pytest

from server.app.pipeline.common import parse_srt, parse_srt_file
from workspace_libs.cms.knowledge import _extract_knowledge_video_url, _parse_knowledge_payload


def test_parse_srt():
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

    data = _parse_knowledge_payload(payload)
    assert data is not None
    assert _extract_knowledge_video_url(data) == ("https://example.com/k001.mp4", "")


def test_parse_time_unknown_format():
    from server.app.pipeline.common import parse_time

    with pytest.raises(ValueError, match="Unknown timestamp"):
        parse_time("invalid")


def test_parse_time_with_single_part():
    from server.app.pipeline.common import parse_time

    with pytest.raises(ValueError, match="Unknown timestamp"):
        parse_time("1")


def test_parse_time_empty_string():
    from server.app.pipeline.common import parse_time

    with pytest.raises(ValueError, match="Unknown timestamp"):
        parse_time("")


def test_parse_time_non_numeric_parts():
    from server.app.pipeline.common import parse_time

    with pytest.raises(ValueError, match="Unknown timestamp"):
        parse_time("ab:cd")


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


def test_parse_srt_file_matches_parse_srt(tmp_path: Path) -> None:
    from server.app.pipeline.common import parse_srt

    text = (
        "1\n00:00:00,000 --> 00:00:01,500\nhello\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\nworld\n\n"
        "3\n00:00:03,000 --> 00:00:04,000\nfoo\n\n"
        "\n\n\n"  # extra blank lines
        "4\n00:00:04,000 --> 00:00:05,000\nbar\n"
    )
    srt_path = tmp_path / "test.srt"
    srt_path.write_text(text, encoding="utf-8")

    expected = parse_srt(text)
    actual = parse_srt_file(srt_path)
    assert actual == expected
    assert len(actual) == 4


def test_parse_srt_file_missing_path() -> None:
    result = parse_srt_file(Path("/nonexistent/path.srt"))
    assert result == []
