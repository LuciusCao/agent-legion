"""Demo workspace seeding for the browser smoke (extracted for the file budget).

Same behavior as the historical inline runner function; see
scripts/seed_demo.py for the seeder itself.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def seed_demo_workspace(dsn: str, vault_key: str, data_dir: Path, project_root: Path) -> None:
    """Provision the demo workspace (id=education_video_problems_generation).

    Schema v62 removed the create-path sample-template seed, so the demo DAG,
    factory Agents, node codes and materials the smoke specs drive are seeded
    up front via the same seeder `make import-demo` uses. The skill lock step
    resolves refs via git, so the repo-shipped demo skills are first imported
    into data_dir (scripts/import-demo.sh via AGENT_LEGION_DEMO_SKILLS_DIR)
    and passed as skill_root. load_settings reads AGENT_LEGION_* from
    os.environ, so the e2e overrides wrap the seed call.
    """
    from scripts.seed_demo import seed_demo
    from server.app.settings import load_settings

    skills_root = data_dir / "demo-skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    imported = subprocess.run(
        [str(project_root / "scripts" / "import-demo.sh")],
        cwd=project_root,
        env={**os.environ, "AGENT_LEGION_DEMO_SKILLS_DIR": str(skills_root)},
        capture_output=True,
        text=True,
    )
    if imported.returncode != 0:
        raise RuntimeError(f"demo skill import failed:\n{imported.stdout}\n{imported.stderr}")

    overrides = {
        "AGENT_LEGION_SKIP_DOTENV": "1",
        "AGENT_LEGION_DATABASE_URL": dsn,
        "AGENT_LEGION_DATA_DIR": str(data_dir),
        "AGENT_LEGION_VAULT_MASTER_KEY": vault_key,
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        seed_demo(load_settings(), skill_root=skills_root)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
