#!/usr/bin/env python3
"""Idempotently seed the repository-shipped demo into the local instance."""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from server.app.agent_catalog import builtin as agent_catalog_builtin
from server.app.jobs import JobQueries
from server.app.services.demo_material_seed import seed_demo_workspace_materials
from server.app.services.demo_node_migration import migrate_demo_node_codes_to_workspaces
from server.app.services.demo_node_seed import seed_demo_workspace_node_codes
from server.app.services.instance_settings import apply_instance_settings
from server.app.services.skill_lock_store import SkillLockStore
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.settings import Settings, load_settings
from server.app.skills.config import LockedSkill, SkillsLock
from server.app.skills.skill_roots import default_skill_base_dir
from server.app.workflows.builtin import load_builtin_workflow

DEMO_WORKFLOW_KEY = "education_video_problems_generation"
DEMO_WORKSPACE_NAME = "教育内容生产 Demo"
DEMO_SKILL_PREFIX = "education-video-problems-generation"
# The demo DAG pins each Agent-routed node's skill at the import tag
# (issue #76, ``server.app.workflows.builtin_demo``); ``make import-demo``
# creates the in-place repos and this tag under the skills root.
DEMO_SKILL_REF = "v1.0.0"
DEMO_SKILL_NAMES = (
    "write-script",
    "review-script",
    "generate-questions",
    "review-questions",
)


@dataclass(frozen=True)
class SeedDemoResult:
    workspace_id: str
    workspace_created: bool
    locks_updated: int
    node_codes_added: int
    agents_added: int
    materials_added: int


def _resolve_commit(repo: Path, ref: str) -> str:
    if not (repo / ".git").is_dir():
        raise RuntimeError(
            f"demo skill repo is missing or is not a git repository: {repo}; "
            "remove the conflicting directory and rerun make import-demo"
        )
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot resolve {ref!r} in demo skill repo {repo}: {result.stderr}")
    return result.stdout.strip()


def _lock_demo_skills(store: SkillLockStore) -> int:
    """Pin ``DEMO_SKILL_REF`` for each demo skill in the DB skill lock.

    The repos are the in-place directories ``make import-demo`` created at
    ``<skills root>/<group>/<name>`` (#322: no source registry, no clone
    channel). Re-pinning in place keeps any other refs already frozen for
    the skill.
    """
    lock = store.get_lock() or SkillsLock()
    updated = 0
    for name in DEMO_SKILL_NAMES:
        key = f"{DEMO_SKILL_PREFIX}/{name}"
        repo = default_skill_base_dir() / DEMO_SKILL_PREFIX / name
        commit = _resolve_commit(repo, DEMO_SKILL_REF)
        entry = lock.skills.get(key)
        if entry is None or entry.refs.get(DEMO_SKILL_REF) != commit:
            if entry is None:
                entry = LockedSkill(repo=str(repo))
            entry.refs[DEMO_SKILL_REF] = commit
            lock.skills[key] = entry
            updated += 1
    if updated:
        lock.resolved_at = (
            datetime.datetime.now(datetime.UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        store.put_lock(lock)
    return updated


def seed_demo(
    settings: Settings,
    *,
    workspace_name: str = DEMO_WORKSPACE_NAME,
) -> SeedDemoResult:
    job_db = JobQueries(settings.database_url, jobs_dir=settings.jobs_dir)
    apply_instance_settings(settings, job_db)

    # Establish the target first, then hydrate the workflow assets into that
    # workspace. Nothing in this onboarding path creates global node code.
    workspaces = [
        workspace
        for workspace in job_db.list_workspaces()
        if workspace.get("default_workflow_key") == DEMO_WORKFLOW_KEY
    ]
    workspace_created = not workspaces
    # Schema v62: the demo workspace id is its workflow key (the find-above
    # lookup keeps working for legacy rows).
    workspace = (
        workspaces[0]
        if workspaces
        else job_db.create_workspace(
            workspace_name,
            default_workflow_key=DEMO_WORKFLOW_KEY,
            default_entity="question",
            workspace_id=DEMO_WORKFLOW_KEY,
        )
    )
    workspace_id = str(workspace["id"])

    locks_updated = _lock_demo_skills(SkillLockStore(job_db))
    node_codes_added = migrate_demo_node_codes_to_workspaces(settings, job_db)
    node_codes_added += len(
        seed_demo_workspace_node_codes(settings, workspace_id, connect_source=job_db)
    )

    # Older demo workspaces may have a revision but lack one of the factory
    # Agents, so seed the two resources independently.
    agents_added = len(
        agent_catalog_builtin.seed_demo_workspace_agent_definitions(job_db, workspace_id)
    )
    # Sample materials (design §9): seed-if-absent; skipped with a warning
    # when object storage is not configured on this instance.
    materials_added = len(
        seed_demo_workspace_materials(settings, workspace_id, connect_source=job_db)
    )
    WorkflowRevisionService(
        job_db,
        settings.executor_runtime.workflows.custom_nodes_enabled,
    ).ensure_active_revision(workspace_id, load_builtin_workflow(DEMO_WORKFLOW_KEY))

    return SeedDemoResult(
        workspace_id=workspace_id,
        workspace_created=workspace_created,
        locks_updated=locks_updated,
        node_codes_added=node_codes_added,
        agents_added=agents_added,
        materials_added=materials_added,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the built-in education content demo")
    parser.add_argument(
        "--workspace-name",
        default=os.environ.get("AGENT_LEGION_DEMO_WORKSPACE_NAME", DEMO_WORKSPACE_NAME),
    )
    args = parser.parse_args()

    result = seed_demo(
        load_settings(),
        workspace_name=args.workspace_name,
    )
    action = "已创建" if result.workspace_created else "已存在，复用"
    print(
        f"[demo seed] workspace {action}: {result.workspace_id}; "
        f"lock 更新 {result.locks_updated}，"
        f"node code 新增 {result.node_codes_added}，Agent 新增 {result.agents_added}，"
        f"材料新增 {result.materials_added}"
    )
    print(
        "[demo seed] 接下来配置 workspace 的 provider/model，并开启 workspace 自动调度与 "
        "Worker claim。"
    )


if __name__ == "__main__":
    main()
