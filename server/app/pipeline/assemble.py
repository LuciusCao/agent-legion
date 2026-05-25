import json
from pathlib import Path

from server.app.pipeline.common import parse_srt
from server.app.pipeline.upload_params import write_upload_params


def assemble_video(video: dict, video_dir: Path) -> dict:
    srt_path = video_dir / "subtitles_reviewed.srt"
    if not srt_path.exists():
        srt_path = video_dir / "subtitles.srt"
    subtitles = parse_srt(srt_path.read_text(encoding="utf-8")) if srt_path.exists() else []
    chapters_data = json.loads((video_dir / "chapters.json").read_text(encoding="utf-8"))
    chapters = chapters_data.get("chapters", []) if isinstance(chapters_data, dict) else chapters_data
    interactions_path = video_dir / "interactions.json"
    if interactions_path.exists():
        interactions_data = json.loads(interactions_path.read_text(encoding="utf-8"))
    else:
        interactions_data = {"version": "1.0", "interactions": []}
        interactions_path.write_text(
            json.dumps(interactions_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    interactions = interactions_data.get("interactions", [])
    duration = subtitles[-1]["end"] if subtitles else 0
    review_path = video_dir / "review_result.json"
    review_details = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    metadata = {
        "video_id": video["id"],
        "title": video.get("title", video["id"]),
        "duration": duration,
        "video_url": video.get("source_url", ""),
        "content_type": video.get("content_type", "knowledge"),
        "external_id": video.get("external_id", ""),
        "knowledge_code": video.get("knowledge_code", ""),
        "question_id": video.get("question_id", ""),
        "status": "已完成",
        "chapters": chapters,
        "interactions": interactions,
        "subtitles": subtitles,
        "review_details": review_details,
    }
    (video_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (video_dir / "report.md").write_text(f"# {metadata['title']}\n\n已完成组装。\n", encoding="utf-8")
    write_upload_params(video, video_dir)
    return metadata
