"""Single source of truth for the on-disk skills root.

The skills root (``~/.agents/skills``) is the base for the skill cache: the
SkillManager, the skill catalog/editing services and the relock CLI all
default to it, and each workspace's agent skills live at
``~/.agents/skills/<workspace_id>/``. A missing cache dir self-heals — the
manager re-clones from the DB skill lock — so moving the base dir needs no
data migration.
"""

from __future__ import annotations

from pathlib import Path

# Display form of the skills root (instance settings API, frontend labels).
SKILLS_ROOT_DISPLAY = "~/.agents/skills"


def skills_root() -> Path:
    """The on-disk skills root: base for the skill cache and workspace skills."""
    return Path.home() / ".agents" / "skills"


def default_skill_base_dir() -> Path:
    """Default base dir for SkillManager / catalog / editing / relock CLI."""
    return skills_root()


def workspace_skill_dir(workspace_id: str) -> Path:
    """A workspace's agent skill directory under the skills root."""
    return skills_root() / workspace_id


def workspace_skill_prefix_display(workspace_id: str) -> str:
    """Display prefix for a workspace's agent skills (API/frontend labels)."""
    return f"{SKILLS_ROOT_DISPLAY}/{workspace_id}/"
