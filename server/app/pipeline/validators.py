import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise ValueError(f"Missing required file: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_phase_outputs(video_dir: Path, phase_key: str) -> None:
    """Validate agent phase output format. Raise ValueError on invalid data."""
    if phase_key == "subtitle_review":
        report = _load_json(video_dir / "subtitle_review_report.json")
        if not isinstance(report, dict):
            raise ValueError("subtitle_review_report.json must be a JSON object")
        srt_path = video_dir / "subtitles_reviewed.srt"
        if not srt_path.exists():
            raise ValueError("subtitles_reviewed.srt is missing after subtitle_review")

    elif phase_key == "chapter_generate":
        data = _load_json(video_dir / "chapters.json")
        chapters = data.get("chapters", []) if isinstance(data, dict) else data
        if not isinstance(chapters, list):
            raise ValueError("chapters.json must contain a list of chapters")
        if not chapters:
            raise ValueError("chapters.json must contain at least one chapter")
        for idx, ch in enumerate(chapters):
            if not isinstance(ch, dict):
                raise ValueError(f"Chapter {idx + 1} must be an object")
            if "end_time" not in ch and "end" not in ch:
                raise ValueError(
                    f"Chapter {idx + 1} ('{ch.get('title', '')}') is missing 'end_time'. "
                    f"The chapter_generate agent must output 'end_time' for every chapter."
                )
            if not ch.get("title"):
                raise ValueError(f"Chapter {idx + 1} is missing 'title'")

    elif phase_key == "interaction_generate":
        data = _load_json(video_dir / "interactions.json")
        interactions = data.get("interactions", []) if isinstance(data, dict) else data
        if not isinstance(interactions, list):
            raise ValueError("interactions.json must contain an 'interactions' array")
        for idx, inter in enumerate(interactions):
            if not isinstance(inter, dict):
                raise ValueError(f"Interaction {idx + 1} must be an object")
            if not inter.get("id"):
                raise ValueError(f"Interaction {idx + 1} is missing 'id'")
            itype = inter.get("type", "")
            if itype not in {"example_practice", "video_summary", "interaction_summary"}:
                raise ValueError(
                    f"Interaction {idx + 1} has unknown type '{itype}'. "
                    f"Expected one of: example_practice, video_summary, interaction_summary"
                )
            if "trigger_time" not in inter:
                raise ValueError(f"Interaction {idx + 1} ('{inter.get('id')}') is missing 'trigger_time'")
            if not inter.get("instruction"):
                raise ValueError(f"Interaction {idx + 1} ('{inter.get('id')}') is missing 'instruction'")

    elif phase_key == "content_review":
        checklist = _load_json(video_dir / "checklist.json")
        if not isinstance(checklist, dict):
            raise ValueError("checklist.json must be a JSON object")
        review = _load_json(video_dir / "review_result.json")
        if not isinstance(review, dict):
            raise ValueError("review_result.json must be a JSON object")
        if "reviews" not in review:
            raise ValueError("review_result.json is missing 'reviews' array")
