from typing import Any, Protocol

from server.app.db import Database
from server.app.pipeline.common import make_record_id
from server.app.pipeline.fetch_url import get_token, lookup_knowledge_video, lookup_question_video
from server.app.records import VideoRecord
from server.app.settings import Settings

CONTENT_TYPES = {"knowledge", "question"}
REJECTED_INTAKE_STATUSES = {"invalid", "not_found", "fetch_failed"}


class VideoInputLike(Protocol):
    url: str
    title: str
    content_type: str
    external_id: str
    source_uuid: str


def normalized_content_type(value: str) -> str:
    return value if value in CONTENT_TYPES else "knowledge"


def resolve_video_input(item: VideoInputLike, settings: Settings) -> tuple[str, str, str, str, str]:
    content_type = normalized_content_type(item.content_type)
    external_id = item.external_id.strip()
    if item.url:
        return "created", item.url.strip(), item.title.strip(), "", ""
    if not external_id:
        return "invalid", "", "", "缺少资源 ID", ""

    cms = settings.config.get("cms", {})
    if not cms:
        return "fetch_failed", "", "", "CMS 配置缺失，无法校验资源是否存在", ""

    token = get_token(cms.get("env", "prod"), cms)
    if content_type == "knowledge":
        lookup = lookup_knowledge_video(external_id, cms.get("knowledge_url"), token)
    else:
        lookup = lookup_question_video(external_id, cms.get("question_url"), token)

    if lookup.status == "not_found":
        return "not_found", "", "", "资源不存在", ""
    if lookup.status == "missing_url":
        return "created_missing_url", "", item.title.strip() or lookup.title, "", lookup.source_uuid
    return "created", lookup.url, item.title.strip() or lookup.title, "", lookup.source_uuid


def add_video_items(
    db: Database,
    settings: Settings,
    items: list[VideoInputLike],
) -> dict[str, Any]:
    videos: list[VideoRecord | None] = []
    results: list[dict[str, Any]] = []

    for item in items:
        content_type = normalized_content_type(item.content_type)
        external_id = item.external_id.strip()

        if external_id:
            existing = db.find_video_by_identity(content_type, external_id)
            if existing:
                results.append(
                    {
                        "external_id": external_id,
                        "content_type": content_type,
                        "status": "duplicate",
                        "message": "资源已在队列中",
                        "video": existing,
                    }
                )
                continue
        elif item.url:
            video_id = make_record_id(item.url.strip(), content_type, external_id)
            existing = db.get_video(video_id)
            if existing:
                results.append(
                    {
                        "external_id": external_id,
                        "content_type": content_type,
                        "status": "duplicate",
                        "message": "资源已在队列中",
                        "video": existing,
                    }
                )
                continue

        try:
            result_status, url, title, message, cms_source_uuid = resolve_video_input(item, settings)
        except Exception as exc:
            results.append(
                {
                    "external_id": external_id,
                    "content_type": content_type,
                    "status": "fetch_failed",
                    "message": str(exc),
                }
            )
            continue

        if result_status in REJECTED_INTAKE_STATUSES:
            results.append(
                {
                    "external_id": external_id,
                    "content_type": content_type,
                    "status": result_status,
                    "message": message,
                }
            )
            continue

        source_uuid = getattr(item, "source_uuid", "") or cms_source_uuid
        video = db.create_video(
            url,
            title,
            content_type=content_type,
            external_id=external_id,
            source_uuid=source_uuid,
        )
        video_dir = settings.videos_dir / video["id"]
        video_dir.mkdir(parents=True, exist_ok=True)
        db.update_video(
            video["id"],
            storage_dir=str(video_dir),
            status="queued" if url else "missing_url",
            current_phase="download" if url else "waiting_for_url",
        )
        saved = db.get_video(video["id"])
        videos.append(saved)
        results.append(
            {
                "external_id": external_id,
                "content_type": content_type,
                "status": result_status,
                "message": "",
                "video": saved,
            }
        )

    return {"videos": videos, "results": results}
