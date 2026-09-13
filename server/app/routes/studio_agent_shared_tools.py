"""Studio-agent shared skill material tool endpoints (issue #633).

Read (``GET .../skills-shared``) and author (``PUT .../skills-shared``)
a workspace's ``_shared`` materials — the shared references/scripts
a workspace's skills consume via ``_shared/map.json``, synced into each
mapped skill's repo at ``save_skill_version`` time (STUDIO-AGENT-001:
draft-only; the sync lands in the LOCAL skill commits, never in the DB
skill lock, and publishing/relocking stays human-only).

The router carries the same guards as the job tools surface
(``require_studio_agent_scope`` + ``require_studio_agent_workspace``) and
is mounted from ``create_studio_agent_skill_tools_router`` (the shared
materials are skill-authoring infrastructure; ``studio_agent_tools.py``
and ``routes/__init__.py`` are at frozen budget ceilings).

``_shared`` is NOT a git repo in v1 — the mapped skills' commits record
the synced copies, which IS the audit trail. The PUT validates
EVERYTHING first (path safety, duplicate resolved paths, map.json
schema, per-file bounds), then applies the full state through the
staged-swap in ``skill_shared_store.write_shared_materials``: readers
see old-or-new (never mixed), a mid-write failure leaves the previous
state in place, and files omitted from the payload disappear (the
replaced directory is the delete pass). GET reads a lock-consistent
snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import (
    require_studio_agent_scope,
    require_studio_agent_workspace,
)
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_agent_shared_contracts import (
    SharedMaterialFile,
    SharedMaterialsResponse,
    SharedMaterialsSaveRequest,
)
from server.app.services.job_errors import NotFoundError
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_shared_put import validate_shared_put_payload
from server.app.services.skill_shared_store import (
    MAP_PATH,
    SHARED_DIR_NAME,
    read_shared_files,
    shared_edit_lock,
    write_shared_materials,
)
from server.app.services.skill_shared_sync import load_shared_map
from server.app.settings import Settings
from server.app.skills.skill_roots import workspace_skill_dir

_MATERIAL_DIRS = ("references", "scripts")


def _shared_dir(job_db: JobQueries, workspace_id: str) -> Path:
    if job_db.get_workspace(workspace_id) is None:
        raise_job_http_error(NotFoundError("Workspace not found"))
    return workspace_skill_dir(workspace_id) / SHARED_DIR_NAME


def _load_map_json(shared_dir: Path) -> dict:
    # kimi review P2-6：损坏的 map.json 在读侧也是结构化 422（同类失败路径
    # 的一致契约），agent 拿到可理解的错误而不是零信息 500。
    try:
        raw = json.loads((shared_dir / MAP_PATH).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise_job_http_error(
            SkillEditValidationError(
                "Invalid shared materials map",
                [{"path": MAP_PATH, "error": f"malformed JSON: {exc}"}],
            )
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise_job_http_error(
            SkillEditValidationError(
                "Invalid shared materials map", [{"path": MAP_PATH, "error": f"unreadable: {exc}"}]
            )
        )
    parsed: dict = raw
    return parsed


def create_studio_agent_shared_tools_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    del settings  # the shared dir resolves from the skills root (HOME)

    router = APIRouter(
        dependencies=[
            Depends(require_studio_agent_scope),
            Depends(require_studio_agent_workspace),
        ]
    )

    @router.get(
        "/studio-agent/tools/workspaces/{workspace_id}/skills-shared",
        response_model=SharedMaterialsResponse,
    )
    def get_shared_materials(workspace_id: str) -> SharedMaterialsResponse:
        shared_dir = _shared_dir(job_db, workspace_id)
        # Lock-consistent snapshot (codex review R2 P1): the map and the
        # files must come from the same generation, never a swap in between.
        # kimi review P2-6：损坏的 map.json（load_shared_map 校验失败）与
        # 读取失败都是结构化 422，不冒泡成零信息 500。
        try:
            with shared_edit_lock(shared_dir, shared_dir.parent.parent):
                if load_shared_map(shared_dir) is None:
                    return SharedMaterialsResponse(workspace_id=workspace_id, map=None, files=[])
                return SharedMaterialsResponse(
                    workspace_id=workspace_id,
                    map=_load_map_json(shared_dir),
                    files=[
                        SharedMaterialFile(**item)
                        for item in read_shared_files(shared_dir, _MATERIAL_DIRS)
                    ],
                )
        except SkillEditValidationError as exc:
            raise_job_http_error(exc)

    @router.put(
        "/studio-agent/tools/workspaces/{workspace_id}/skills-shared",
        response_model=SharedMaterialsResponse,
    )
    def save_shared_materials(
        workspace_id: str, payload: SharedMaterialsSaveRequest
    ) -> SharedMaterialsResponse:
        shared_dir = _shared_dir(job_db, workspace_id)
        # Full-state PUT: every rule (paths, UTF-8 byte caps, map shape,
        # mapped-source completeness — codex R2/R3) runs before the staged
        # swap touches disk; a validation failure leaves the live dir alone.
        try:
            targets = validate_shared_put_payload(
                shared_dir, [(item.path, item.content) for item in payload.files]
            )
        except SkillEditValidationError as exc:
            raise_job_http_error(exc)
        write_shared_materials(shared_dir, list(targets.items()), shared_dir.parent.parent)
        return get_shared_materials(workspace_id)

    return router
