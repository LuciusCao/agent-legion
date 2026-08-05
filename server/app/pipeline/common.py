"""SRT subtitle parsing/formatting, backed by the ``srt`` library.

The public functions keep their historical signatures: ``parse_srt`` /
``parse_srt_file`` return ``list[dict]`` with ``index``/``start``/``end``/
``text`` fields (seconds as float), which callers unpack directly into
``VideoSubtitleResponse``.
"""

from datetime import timedelta
from pathlib import Path

import srt


def parse_time(value: str) -> float:
    text = value.strip().replace(",", ".")
    try:
        return float(srt.srt_timestamp_to_timedelta(text).total_seconds())
    except srt.TimestampParseError:
        pass
    # srt 只接受 HH:MM:SS 三段式时间戳；兼容历史行为保留 MM:SS 两段式。
    parts = text.split(":")
    if len(parts) == 2:
        try:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
        except ValueError:
            pass
    raise ValueError(f"Unknown timestamp: {value}") from None


def parse_srt(text: str) -> list[dict]:
    """Parse SRT text, silently skipping garbled blocks (historical behavior)."""
    subtitles: list[dict] = []
    for subtitle in srt.parse(text, ignore_errors=True):
        subtitles.append(
            {
                "index": subtitle.index if subtitle.index is not None else len(subtitles) + 1,
                "start": subtitle.start.total_seconds(),
                "end": subtitle.end.total_seconds(),
                # 历史实现对每行 strip 并丢弃空行，保持该输出契约。
                "text": "\n".join(
                    line.strip() for line in subtitle.content.splitlines() if line.strip()
                ),
            }
        )
    return subtitles


def parse_srt_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return parse_srt(path.read_text(encoding="utf-8"))


def format_srt_time(seconds: float) -> str:
    return str(srt.timedelta_to_srt_timestamp(timedelta(seconds=seconds)))
