from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.cms.client import get_token
from server.app.cms.question import CmsQuestionDetail, fetch_question_detail
from server.app.pipelines.resources import resolve_cms_resource


def _decode_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _effective_cms_config(job: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    settings_config = context.get("settings_config")
    if not isinstance(settings_config, dict):
        settings_config = {}
    job_db = context.get("job_db")
    workspace = None
    batch_payload = None
    if job_db is not None:
        workspace = job_db.get_workspace(str(job.get("workspace_id", "default")))
        batch = job_db.get_batch(str(job.get("batch_id", "")))
        if batch:
            batch_payload = _decode_json_object(batch.get("source_payload_json"))
    return resolve_cms_resource(settings_config, workspace, batch_payload, "question_detail")


def _base_payload(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": job["source_id"],
        "title": job["title"],
        "source_type": job["source_type"],
        "normalized": {},
        "cms_payload": None,
    }


def _payload_from_detail(job: dict[str, Any], detail: CmsQuestionDetail) -> dict[str, Any]:
    return {
        "question_id": detail.question_id or job["source_id"],
        "title": detail.title or job["title"],
        "source_type": job["source_type"],
        "normalized": detail.normalized,
        "cms_payload": detail.payload,
    }


def fetch_question_context(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    cms_config = _effective_cms_config(job, context)
    api_url = cms_config.get("api_url") or cms_config.get("question_detail_url")
    if api_url:
        token = get_token(str(cms_config.get("env", "")), cms_config)
        detail = fetch_question_detail(str(job["source_id"]), str(api_url), token)
        payload = _payload_from_detail(job, detail)
    else:
        payload = _base_payload(job)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "question_context.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
