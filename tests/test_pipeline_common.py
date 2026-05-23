from server.app.pipeline.common import get_video_id, parse_srt
from server.app.pipeline.fetch_url import _extract_knowledge_url


def test_parse_srt_and_video_id():
    assert get_video_id("https://cdn.example.com/videos/g02060101.mp4?x=1") == "g02060101"

    subtitles = parse_srt(
        "1\n00:00:00,000 --> 00:00:01,500\n你好\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\n继续\n"
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

    assert _extract_knowledge_url("K001", payload) == "https://example.com/k001.mp4"
