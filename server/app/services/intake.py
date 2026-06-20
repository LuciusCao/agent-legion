from collections.abc import Sequence
from typing import Any, Protocol, cast

from server.app.cms.client import get_token
from server.app.cms.knowledge import lookup_knowledge_video
from server.app.cms.question import lookup_question_video
from server.app.db import Database
from server.app.pipeline.common import make_record_id
from server.app.records import VideoRecord
from server.app.security import validate_download_url
from server.app.services.video_read import project_video_storage_dir
from server.app.settings import Settings
from server.app.storage_paths import make_data_relative

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
        url = item.url.strip()
        try:
            validate_download_url(url)
        except ValueError as exc:
            return "invalid", "", item.title.strip(), f"URL 校验失败: {exc}", ""
        return "created", url, item.title.strip(), "", ""
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
    items: Sequence[VideoInputLike],
) -> dict[str, Any]:
    videos: list[VideoRecord | None] = []
    results: list[dict[str, Any]] = []

    # Batch pre-check duplicates (issue 012)
    identities: list[tuple[str, str]] = []
    url_only_video_ids: list[str] = []
    for item in items:
        content_type = normalized_content_type(item.content_type)
        external_id = item.external_id.strip()
        if external_id:
            identities.append((content_type, external_id))
        elif item.url:
            url_only_video_ids.append(make_record_id(item.url.strip(), content_type, external_id))

    existing_by_identity = db.find_videos_by_identities(identities) if identities else {}
    existing_by_id = (
        {v["id"]: v for v in db.batch_get_videos(url_only_video_ids)} if url_only_video_ids else {}
    )

    for item in items:
        content_type = normalized_content_type(item.content_type)
        external_id = item.external_id.strip()

        if external_id:
            existing = existing_by_identity.get((content_type, external_id))
            if existing:
                projected = project_video_storage_dir(existing, settings)
                results.append(
                    {
                        "external_id": external_id,
                        "content_type": content_type,
                        "status": "duplicate",
                        "message": "资源已在队列中",
                        "video": projected,
                    }
                )
                continue
        elif item.url:
            video_id = make_record_id(item.url.strip(), content_type, external_id)
            existing = existing_by_id.get(video_id)
            if existing:
                projected = project_video_storage_dir(existing, settings)
                results.append(
                    {
                        "external_id": external_id,
                        "content_type": content_type,
                        "status": "duplicate",
                        "message": "资源已在队列中",
                        "video": projected,
                    }
                )
                continue

        try:
            result_status, url, title, message, cms_source_uuid = resolve_video_input(
                item, settings
            )
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
            storage_dir=make_data_relative(video_dir, settings.data_dir),
            status="queued" if url else "missing_url",
            current_phase="download" if url else "waiting_for_url",
        )
        saved = db.get_video(video["id"])
        if saved is not None:
            saved = cast(VideoRecord, project_video_storage_dir(saved, settings))
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
