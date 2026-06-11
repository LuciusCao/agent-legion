from __future__ import annotations

import sys
from types import ModuleType

# Some environments do not have funasr installed. Mock it before importing the
# module so that the top-level import does not call sys.exit(1).
if "funasr" not in sys.modules:
    _funasr = ModuleType("funasr")
    _funasr.AutoModel = object
    sys.modules["funasr"] = _funasr

from server.app.pipeline.transcribe_sensevoice import (
    format_time,
    merge_short_segments,
    split_by_punctuation,
)


def test_split_by_punctuation_splits_on_punctuation() -> None:
    words = ["这", "是", "测", "试", "。"]
    # Duration of the current segment reaches 1.0s exactly at the punctuation.
    timestamps = [[0, 200], [200, 400], [400, 600], [600, 800], [800, 1800]]

    segments = split_by_punctuation(words, timestamps)

    assert len(segments) == 1
    assert segments[0]["text"] == "这是测试。"
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 1.8


def test_split_by_punctuation_splits_at_max_duration() -> None:
    words = ["a", "b", "c"]
    # The second character pushes the segment duration over the max.
    timestamps = [[0, 2500], [2500, 6500], [6500, 7000]]

    segments = split_by_punctuation(words, timestamps, max_duration=6.0)

    assert len(segments) == 2
    assert segments[0]["text"] == "ab"
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 6.5
    assert segments[1]["text"] == "c"
    assert segments[1]["start"] == 6.5
    assert segments[1]["end"] == 7.0


def test_split_by_punctuation_appends_remaining_text() -> None:
    words = ["x", "y"]
    timestamps = [[0, 300], [300, 600]]

    segments = split_by_punctuation(words, timestamps)

    assert len(segments) == 1
    assert segments[0]["text"] == "xy"
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 0.6


def test_merge_short_segments_combines_short_neighbors() -> None:
    segments = [
        {"start": 0.0, "end": 0.3, "text": "a"},
        {"start": 0.35, "end": 1.5, "text": "b"},
    ]

    merged = merge_short_segments(segments, min_duration=0.8)

    assert len(merged) == 1
    assert merged[0]["text"] == "ab"
    assert merged[0]["start"] == 0.0
    assert merged[0]["end"] == 1.5


def test_merge_short_segments_preserves_long_segments_and_large_gaps() -> None:
    segments = [
        {"start": 0.0, "end": 1.0, "text": "long"},
        {"start": 2.0, "end": 2.2, "text": "short"},
        {"start": 2.3, "end": 3.0, "text": "next"},
    ]

    merged = merge_short_segments(segments, min_duration=0.8)

    assert len(merged) == 2
    assert merged[0]["text"] == "long"
    assert merged[1]["text"] == "shortnext"
    assert merged[1]["end"] == 3.0


def test_merge_short_segments_empty() -> None:
    assert merge_short_segments([]) == []


def test_format_time() -> None:
    assert format_time(0.0) == "00:00:00,000"
    assert format_time(3661.123) == "01:01:01,123"
    assert format_time(59.999) == "00:00:59,999"
    assert format_time(60.5) == "00:01:00,500"
