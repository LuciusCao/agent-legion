"""Unit tests for workspace_libs/media.py (SRT parsing + ffprobe duration)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workspace_libs.media import get_video_duration, parse_srt

pytestmark = pytest.mark.no_db


def test_parse_srt_basic() -> None:
    text = "1\n00:00:01,000 --> 00:00:02,500\n你好\n\n2\n00:00:03.000 --> 00:00:04,000\n世界\n\n"
    assert parse_srt(text) == [
        {"index": 1, "start": 1.0, "end": 2.5, "text": "你好"},
        {"index": 2, "start": 3.0, "end": 4.0, "text": "世界"},
    ]


def test_parse_srt_multiline_stripped_and_blank_dropped() -> None:
    text = "1\n00:00:01,000 --> 00:00:02,000\n  第一行  \n\n\n第二行\n\n"
    assert parse_srt(text) == [{"index": 1, "start": 1.0, "end": 2.0, "text": "第一行\n第二行"}]


def test_parse_srt_skips_garbled_blocks() -> None:
    text = "not a subtitle block\n\n7\n00:00:05,000 --> 00:00:06,000\n有效\n\n"
    assert parse_srt(text) == [{"index": 7, "start": 5.0, "end": 6.0, "text": "有效"}]


def test_parse_srt_fullwidth_delimiters_and_crlf() -> None:
    text = "3\r\n00：00：01。500 --> 00：00：02，000\r\n全角分隔\r\n\r\n"
    assert parse_srt(text) == [{"index": 3, "start": 1.5, "end": 2.0, "text": "全角分隔"}]


def test_parse_srt_missing_index_falls_back_to_position() -> None:
    text = "00:00:01,000 --> 00:00:02,000\n无索引\n\n"
    assert parse_srt(text)[0]["index"] == 1


def test_parse_srt_decimal_index_truncates() -> None:
    text = "12.9\n00:00:01,000 --> 00:00:02,000\n小数索引\n\n"
    assert parse_srt(text)[0]["index"] == 12


def test_parse_srt_unknown_timestamp_raises() -> None:
    # A block whose timestamps the entry regex accepts but the parseable
    # timestamp regex rejects cannot occur; garbage simply never matches.
    assert parse_srt("01:02 --> 03:04\nx\n") == []


def test_get_video_duration_parses_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="12.34\n"),
    )
    assert get_video_duration(Path("v.mp4")) == 12.34


def test_get_video_duration_failure_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=MagicMock(returncode=1)))
    assert get_video_duration(Path("v.mp4")) == 0.0

    def boom(*a: object, **kw: object) -> None:
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(subprocess, "run", boom)
    assert get_video_duration(Path("v.mp4")) == 0.0
