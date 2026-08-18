"""Media utilities for workflow code nodes (framework layer).

Business-free helpers shared by media-handling nodes: an SRT subtitle parser
(vendored from the ``srt`` library's regexes + parse loop, byte-compatible
with ``srt.parse(text, ignore_errors=True)``) and an ffprobe-based duration
probe. Subtitle *quality* policy (coverage/gap thresholds) is business
semantics and deliberately stays in the nodes.

Layering rule: standard library only; never import ``server.app.*``.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_SRT_DELIM = r"[,.:，．。：]"
_SRT_TS = _SRT_DELIM.join([r"[0-9]+"] * 3) + _SRT_DELIM + "?" + r"[0-9]*"
_SRT_TS_PARSEABLE = "^" + _SRT_DELIM.join(["([0-9]+)"] * 3) + _SRT_DELIM + "?" + "([0-9]*)"
_TS_RE = re.compile(_SRT_TS_PARSEABLE)
_SRT_RE_BODY = (
    r"\s*(?:(-?[0-9]+\.?[0-9]*)\s*\r?\n)?"
    r"({ts}) *-[ -] *> *({ts}) ?([^\r\n]*)"
    r"(?:\r?\n|\Z)(.*?)(?:\r?\n|\Z)"
    r"(?:\r?\n|\Z|(?=(?:-?[0-9]+\.?[0-9]*\s*\r?\n{ts})))"
    r"(?=(?:(?:-?[0-9]+\.?[0-9]*\s*\r?\n)?{ts}|\Z))"
)
# Vendored srt regex: the braces are regex syntax, so no f-string.
_SRT_RE = re.compile(_SRT_RE_BODY.format(ts=_SRT_TS), re.DOTALL)  # noqa: UP032


def _timestamp_seconds(value: str) -> float:
    """srt.srt_timestamp_to_timedelta equivalent (millis as whole ms)."""
    match = _TS_RE.match(value)
    if match is None:
        raise ValueError(f"Unknown timestamp: {value}")
    hours, minutes, seconds, millis = [int(g) if g else 0 for g in match.groups()]
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def parse_srt(text: str) -> list[dict]:
    """Parse SRT text, silently skipping garbled blocks (historical behavior).

    Output contract: ``index``/``start``/``end``/``text`` (seconds as float),
    each content line stripped and empty lines dropped — identical to
    ``srt.parse(text, ignore_errors=True)`` in the retired pipeline.
    """
    subtitles: list[dict] = []
    for match in _SRT_RE.finditer(text):
        raw_index, raw_start, raw_end, _proprietary, content = match.groups()
        content = content.replace("\r\n", "\n")
        index = None
        if raw_index is not None:
            try:
                index = int(raw_index)
            except ValueError:
                # Index like 123.4 (rare); same fallback as srt.parse.
                index = int(raw_index.split(".")[0])
        subtitles.append(
            {
                "index": index if index is not None else len(subtitles) + 1,
                "start": _timestamp_seconds(raw_start),
                "end": _timestamp_seconds(raw_end),
                # The historical implementation strips each line and drops
                # empty ones; keep that output contract.
                "text": "\n".join(line.strip() for line in content.splitlines() if line.strip()),
            }
        )
    return subtitles


def get_video_duration(video_path: Path) -> float:
    """Probe the actual media duration via ffprobe; 0.0 when probing fails."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception as exc:
        logger.warning("Failed to probe video duration for %s: %s", video_path, exc)
    return 0.0
