import json
import subprocess
from pathlib import Path

from server.app.pipeline.common import parse_srt
from server.app.pipeline.upload_params import write_upload_params


def get_video_duration(video_path: Path) -> float:
    """Get actual video duration via ffprobe."""
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
    except Exception:
        pass
    return 0.0


def assemble_video(video: dict, video_dir: Path) -> dict:
    srt_path = video_dir / "subtitles_reviewed.srt"
    if not srt_path.exists():
        srt_path = video_dir / "subtitles.srt"
    subtitles = parse_srt(srt_path.read_text(encoding="utf-8")) if srt_path.exists() else []
    chapters_data = json.loads((video_dir / "chapters.json").read_text(encoding="utf-8"))
    chapters = (
        chapters_data.get("chapters", []) if isinstance(chapters_data, dict) else chapters_data
    )
    interactions_path = video_dir / "interactions.json"
    if interactions_path.exists():
        interactions_data = json.loads(interactions_path.read_text(encoding="utf-8"))
    else:
        interactions_data = {"version": "1.0", "interactions": []}
        interactions_path.write_text(
            json.dumps(interactions_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    interactions = interactions_data.get("interactions", [])
    # Use actual video duration from ffprobe, fallback to last subtitle end
    video_path = video_dir / f"{video['id']}.mp4"
    duration = (
        get_video_duration(video_path)
        if video_path.exists()
        else (subtitles[-1]["end"] if subtitles else 0)
    )
    review_path = video_dir / "review_result.json"
    review_details = (
        json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    )
    metadata = {
        "video_id": video["id"],
        "title": video.get("title", video["id"]),
        "duration": duration,
        "video_url": video.get("source_url", ""),
        "content_type": video.get("content_type", "knowledge"),
        "external_id": video.get("external_id", ""),
        "knowledge_code": video.get("knowledge_code", ""),
        "question_id": video.get("question_id", ""),
        "source_uuid": video.get("source_uuid", ""),
        "status": "已完成",
        "chapters": chapters,
        "interactions": interactions,
        "subtitles": subtitles,
        "review_details": review_details,
    }
    (video_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (video_dir / "report.md").write_text(
        f"# {metadata['title']}\n\n已完成组装。\n", encoding="utf-8"
    )
    write_upload_params(video, video_dir)
    return metadata
