from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.cms.client import get_token
from server.app.cms.question import CmsQuestionDetail, fetch_question_detail
from server.app.executors.cancellation import check_cancellation
from server.app.workflows.resources import resolve_cms_resource


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
    check_cancellation(context)
    cms_config = _effective_cms_config(job, context)
    api_url = cms_config.get("api_url") or cms_config.get("question_detail_url")
    if api_url:
        token = get_token(str(cms_config.get("env", "")), cms_config)
        detail = fetch_question_detail(str(job["source_id"]), str(api_url), token)
        check_cancellation(context)
        payload = _payload_from_detail(job, detail)
    else:
        payload = _base_payload(job)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "question_context.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def assemble_package(
    job: dict[str, Any],
    artifact_dir: Path,
    context: dict[str, Any] | None = None,
) -> None:
    """Assemble final upload_params.json and manifest.json for a question content job."""
    inputs = [
        "question_context.json",
        "understanding.json",
        "natural_reading.md",
        "misconceptions.json",
        "solution_steps.json",
        "faq.json",
        "content_graph.json",
        "interactive_template.json",
        "review_result.json",
    ]

    manifest: dict[str, Any] = {
        "question_id": job.get("source_id"),
        "source_type": job.get("source_type"),
        "title": job.get("title"),
        "artifacts": {},
    }
    upload_params: dict[str, Any] = {
        "question_id": job.get("source_id"),
        "source_type": job.get("source_type"),
        "title": job.get("title"),
        "artifacts": {},
    }

    for name in inputs:
        check_cancellation(context)
        path = artifact_dir / name
        if path.is_file():
            if name.endswith(".md"):
                content = path.read_text(encoding="utf-8")
            else:
                content = json.loads(path.read_text(encoding="utf-8"))
            manifest["artifacts"][name] = {"present": True, "size": path.stat().st_size}
            upload_params["artifacts"][name] = content
        else:
            manifest["artifacts"][name] = {"present": False}
            upload_params["artifacts"][name] = None

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "upload_params.json").write_text(
        json.dumps(upload_params, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
