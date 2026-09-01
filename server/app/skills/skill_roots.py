"""Single source of truth for the on-disk skills root.

The skills root (``~/.agents/skills``) is the base for the skill cache: the
SkillManager, the skill catalog/editing services and the relock CLI all
default to it, and each workspace's agent skills live at
``~/.agents/skills/<workspace_id>/``. Moving the base dir abandons the old
cache: the next dispatch re-clones from the DB skill lock. That self-heal is
a local-development feature only — under a read-only container mount (e.g.
the compose ``:ro`` skills mount) re-clone is not possible.
"""

from __future__ import annotations

import re
from pathlib import Path

# Display form of the skills root (instance settings API, frontend labels).
SKILLS_ROOT_DISPLAY = "~/.agents/skills"

# Workspace ids double as directory names under the skills root, so they are
# validated against the schema v62 workspace id contract — which excludes
# empty, absolute, separator-bearing and ``..`` ids by construction.
_WORKSPACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def skills_root() -> Path:
    """The on-disk skills root: base for the skill cache and workspace skills."""
    return Path.home() / ".agents" / "skills"


def default_skill_base_dir() -> Path:
    """Default base dir for SkillManager / catalog / editing / relock CLI."""
    return skills_root()


def _validate_workspace_id(workspace_id: str) -> None:
    if not _WORKSPACE_ID_RE.fullmatch(workspace_id):
        msg = f"workspace id must match ^[a-z0-9][a-z0-9_-]{{0,63}}$: {workspace_id!r}"
        raise ValueError(msg)


def workspace_skill_dir(workspace_id: str) -> Path:
    """A workspace's agent skill directory under the skills root."""
    _validate_workspace_id(workspace_id)
    return skills_root() / workspace_id


def workspace_skill_prefix_display(workspace_id: str) -> str:
    """Display prefix for a workspace's agent skills (API/frontend labels)."""
    _validate_workspace_id(workspace_id)
    return f"{SKILLS_ROOT_DISPLAY}/{workspace_id}/"
