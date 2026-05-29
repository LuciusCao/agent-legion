from pathlib import Path

from server.app.pipeline.phases import PHASES

PHASE_OUTPUTS = {
    "download": ["{video_id}.mp4"],
    "transcribe": ["subtitles.srt", "transcription.json"],
    "subtitle_review": ["subtitles_reviewed.srt", "subtitle_review_report.json"],
    "chapter_generate": ["chapters_raw.json", "chapters.json"],
    "interaction_generate": ["interactions.json"],
    "content_review": ["checklist.json", "review_result.json"],
    "assemble": ["metadata.json", "report.md"],
}

PHASE_ORDER = list(PHASES)


def clear_artifacts_from(video_dir: Path, phase: str, video_id: str) -> None:
    if phase not in PHASE_ORDER:
        raise ValueError(f"Unknown phase: {phase}")
    start = PHASE_ORDER.index(phase)
    for phase_key in PHASE_ORDER[start:]:
        for pattern in PHASE_OUTPUTS[phase_key]:
            path = video_dir / pattern.format(video_id=video_id)
            if path.exists():
                path.unlink()
