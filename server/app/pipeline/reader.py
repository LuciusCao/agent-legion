import json
from pathlib import Path

from server.app.pipeline.common import parse_srt


def read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_artifacts(video_dir: Path) -> dict:
    srt_path = video_dir / "subtitles_reviewed.srt"
    if not srt_path.exists():
        srt_path = video_dir / "subtitles.srt"
    subtitles = parse_srt(srt_path.read_text(encoding="utf-8")) if srt_path.exists() else []
    chapters_data = read_json(video_dir / "chapters.json") or []
    chapters = chapters_data.get("chapters", []) if isinstance(chapters_data, dict) else chapters_data
    interactions_data = read_json(video_dir / "interactions.json") or {}
    interactions = interactions_data.get("interactions", []) if isinstance(interactions_data, dict) else []
    metadata = read_json(video_dir / "metadata.json")
    review = read_json(video_dir / "review_result.json")
    return {
        "subtitles": subtitles,
        "chapters": chapters,
        "interactions": interactions,
        "metadata": metadata,
        "review": review,
    }

