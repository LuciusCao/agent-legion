import re
from pathlib import Path
from urllib.parse import urlparse


def get_video_id(url: str) -> str:
    path = urlparse(url).path
    name = Path(path).name or "video"
    stem = Path(name).stem or name
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "video"


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_")


def make_record_id(source_url: str, content_type: str, external_id: str) -> str:
    normalized = normalize_identifier(external_id)
    if normalized:
        return f"{content_type}_{normalized}"
    if source_url:
        return get_video_id(source_url)
    return "video"


def parse_time(value: str) -> float:
    parts = value.strip().replace(",", ".").split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"Unknown timestamp: {value}")


def parse_srt(text: str) -> list[dict]:
    subtitles: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        match = re.match(
            r"(\d{1,2}:\d{2}(?::\d{2})?[,.]\d{3})\s+-->\s+"
            r"(\d{1,2}:\d{2}(?::\d{2})?[,.]\d{3})",
            lines[1],
        )
        if not match:
            continue
        subtitles.append(
            {
                "index": int(lines[0]) if lines[0].isdigit() else len(subtitles) + 1,
                "start": parse_time(match.group(1)),
                "end": parse_time(match.group(2)),
                "text": "\n".join(lines[2:]),
            }
        )
    return subtitles


def format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
