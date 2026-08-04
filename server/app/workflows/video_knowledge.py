from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.pipeline.assemble import assemble_video
from server.app.pipeline.download import download_video as legacy_download_video
from server.app.pipeline.transcribe import run_transcription_with_providers
from server.app.services.transcription_providers import build_default_providers
from server.app.settings import load_settings
from server.app.video_capabilities.contracts import VideoKnowledgeInput
from server.app.workflows.video_knowledge_source import resolve_knowledge_source


def _load_video_input(job_dir: Path) -> VideoKnowledgeInput:
    raw = json.loads((job_dir / "video_input.json").read_text(encoding="utf-8"))
    return VideoKnowledgeInput.from_mapping(raw)


def _video_id(video_input: VideoKnowledgeInput, job: dict[str, Any]) -> str:
    return video_input.legacy_video_id or str(job.get("id") or "") or video_input.external_id


def download_video(
    job: dict[str, Any], job_dir: Path, runtime: dict[str, Any] | None = None
) -> None:
    video_input = _load_video_input(job_dir)
    if not video_input.source_url:
        video_input = resolve_knowledge_source(job, job_dir, video_input, runtime or {})
    output_path = job_dir / "source.mp4"
    legacy_download_video(video_input.source_url, output_path)


def transcribe_video(
    job: dict[str, Any], job_dir: Path, runtime: dict[str, Any] | None = None
) -> None:
    settings = load_settings()
    if runtime and runtime.get("settings_config"):
        settings.config.update(runtime["settings_config"])
    video_input = _load_video_input(job_dir)
    mode = str(settings.config.get("asr", {}).get("provider", "auto"))
    result = run_transcription_with_providers(
        video_path=job_dir / "source.mp4",
        output_dir=job_dir,
        title=video_input.title,
        duration=0,
        mode=mode,
        providers=build_default_providers(settings),
    )
    (job_dir / "transcription.json").write_text(
        json.dumps(result.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_interactions(video_dir: Path) -> None:
    """Ensure interactions.json uses the dict shape legacy assemble_video expects."""
    interactions_path = video_dir / "interactions.json"
    if not interactions_path.exists():
        return
    data = json.loads(interactions_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        interactions_path.write_text(
            json.dumps({"version": "1.0", "interactions": data}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def assemble_video_metadata(
    job: dict[str, Any], job_dir: Path, runtime: dict[str, Any] | None = None
) -> None:
    video_input = _load_video_input(job_dir)
    legacy_video = {
        "id": _video_id(video_input, job),
        "title": video_input.title,
        "source_url": video_input.source_url,
        "content_type": "knowledge",
        "external_id": video_input.external_id,
        "source_uuid": video_input.source_uuid,
    }
    _normalize_interactions(job_dir)
    assemble_video(legacy_video, job_dir)
    if not (job_dir / "report.md").exists():
        (job_dir / "report.md").write_text(
            f"# {video_input.title or legacy_video['id']}\n", encoding="utf-8"
        )


def package_video_job(
    job: dict[str, Any], job_dir: Path, runtime: dict[str, Any] | None = None
) -> None:
    files = sorted(
        path.name
        for path in job_dir.iterdir()
        if path.is_file() and path.name != "package_manifest.json"
    )
    manifest = {
        "workflow_key": "video_knowledge",
        "job_id": str(job.get("id") or ""),
        "files": files,
    }
    (job_dir / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
