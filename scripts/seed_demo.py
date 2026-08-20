#!/usr/bin/env python3
"""Idempotently seed the repository-shipped demo into the local instance."""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from server.app import agent_catalog_builtin
from server.app.jobs import JobQueries
from server.app.services.demo_node_seed import seed_demo_node_codes
from server.app.services.instance_settings import apply_instance_settings
from server.app.services.skill_source_store import SkillSourceStore
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.settings import Settings, load_settings
from server.app.skills.builtin_sources import BUILTIN_SKILL_SOURCES
from server.app.skills.config import LockedSkillSource, SkillsConfig, SkillsLock, SkillSourceConfig
from server.app.skills.seed import seed_skill_sources
from server.app.workflows.builtin import load_builtin_workflow

DEMO_WORKFLOW_KEY = "education_video_problems_generation"
DEMO_WORKSPACE_NAME = "教育内容生产 Demo"
DEMO_SKILL_PREFIX = "education-video-problems-generation"
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
    sources_added: int
    locks_updated: int
    node_codes_added: int
    agents_added: int


def _factory_sources() -> dict[str, SkillSourceConfig]:
    return {
        key: source.model_copy(deep=True)
        for key, source in BUILTIN_SKILL_SOURCES.skills.items()
        if key.startswith(f"{DEMO_SKILL_PREFIX}/")
    }


def _desired_sources(skill_root: Path | None) -> dict[str, SkillSourceConfig]:
    factory = _factory_sources()
    if skill_root is None:
        return factory
    root = skill_root.expanduser().resolve()
    return {
        f"{DEMO_SKILL_PREFIX}/{name}": SkillSourceConfig(
            repo=str(root / name),
            ref=factory[f"{DEMO_SKILL_PREFIX}/{name}"].ref,
        )
        for name in DEMO_SKILL_NAMES
    }


def _merge_demo_sources(
    store: SkillSourceStore, desired: dict[str, SkillSourceConfig]
) -> tuple[SkillsConfig, int]:
    config = store.get_sources() or SkillsConfig()
    factory = _factory_sources()
    added = 0
    changed = False
    for key, source in desired.items():
        current = config.skills.get(key)
        if current is None:
            config.skills[key] = source
            added += 1
            changed = True
        elif current == factory[key] and current != source:
            # An explicit target may replace only untouched factory values.
            # Operator-customized sources remain authoritative.
            config.skills[key] = source
            changed = True
    if changed:
        store.put_sources(config)
    return config, added


def _local_repo_path(repo: str) -> Path | None:
    if repo.startswith("~/") or Path(repo).is_absolute():
        return Path(repo).expanduser().resolve()
    return None


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


def _lock_local_demo_sources(store: SkillSourceStore, config: SkillsConfig) -> int:
    lock = store.get_lock() or SkillsLock()
    updated = 0
    for name in DEMO_SKILL_NAMES:
        key = f"{DEMO_SKILL_PREFIX}/{name}"
        source = config.skills[key]
        repo = _local_repo_path(source.repo)
        if repo is None:
            # Remote/custom sources are owned by the admin relock flow.
            continue
        desired = LockedSkillSource(
            repo=source.repo,
            ref=source.ref,
            commit=_resolve_commit(repo, source.ref),
        )
        if lock.skills.get(key) != desired:
            lock.skills[key] = desired
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
    skill_root: Path | None = None,
    workspace_name: str = DEMO_WORKSPACE_NAME,
) -> SeedDemoResult:
    job_db = JobQueries(settings.database_url, jobs_dir=settings.jobs_dir)
    apply_instance_settings(settings, job_db.path)
    node_codes_added = len(seed_demo_node_codes(settings))

    seed_skill_sources(settings.database_url, settings.root_dir)
    store = SkillSourceStore(settings.database_url)
    sources, sources_added = _merge_demo_sources(store, _desired_sources(skill_root))
    locks_updated = _lock_local_demo_sources(store, sources)

    workspaces = [
        workspace
        for workspace in job_db.list_workspaces()
        if workspace.get("default_workflow_key") == DEMO_WORKFLOW_KEY
    ]
    workspace_created = not workspaces
    workspace = (
        workspaces[0]
        if workspaces
        else job_db.create_workspace(
            workspace_name,
            default_workflow_key=DEMO_WORKFLOW_KEY,
            default_entity="question",
        )
    )
    workspace_id = str(workspace["id"])

    # Older demo workspaces may have a revision but lack one of the factory
    # Agents, so seed the two resources independently.
    agents_added = len(
        agent_catalog_builtin.seed_demo_workspace_agent_definitions(
            settings.database_url, workspace_id
        )
    )
    WorkflowRevisionService(
        job_db,
        settings.executor_runtime.workflows.custom_nodes_enabled,
    ).ensure_active_revision(workspace_id, load_builtin_workflow(DEMO_WORKFLOW_KEY))

    return SeedDemoResult(
        workspace_id=workspace_id,
        workspace_created=workspace_created,
        sources_added=sources_added,
        locks_updated=locks_updated,
        node_codes_added=node_codes_added,
        agents_added=agents_added,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the built-in education content demo")
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=os.environ.get("AGENT_LEGION_DEMO_SKILLS_DIR"),
        help="root containing the four imported demo skill repositories",
    )
    parser.add_argument(
        "--workspace-name",
        default=os.environ.get("AGENT_LEGION_DEMO_WORKSPACE_NAME", DEMO_WORKSPACE_NAME),
    )
    args = parser.parse_args()

    result = seed_demo(
        load_settings(),
        skill_root=args.skills_dir,
        workspace_name=args.workspace_name,
    )
    action = "已创建" if result.workspace_created else "已存在，复用"
    print(
        f"[demo seed] workspace {action}: {result.workspace_id}; "
        f"skill source 新增 {result.sources_added}，lock 更新 {result.locks_updated}，"
        f"node code 新增 {result.node_codes_added}，Agent 新增 {result.agents_added}"
    )
    print(
        "[demo seed] 接下来配置 workspace 的 provider/model，并开启 workspace 自动调度与 "
        "Worker claim。"
    )


if __name__ == "__main__":
    main()
