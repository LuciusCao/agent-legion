"""CONFIG-YAML-001 evidence: tracked config yaml files carry no secret values.

Scans the tracked split configuration files and asserts the hygiene red lines
hold in the repository itself (the startup-side rejections are covered by
tests/test_settings.py and tests/test_configuration_loader.py):

- the global ``cms:`` section stays retired — CMS defaults live in the
  capability ``config_schema``; base_url/token arrive via env or workspace
  node config (retired ``cms.token`` / ``cms.token_gen``, config governance
  G2, can never reappear);
- ``openclaw.skill_safety.repos`` entries stay a pure path allowlist — refs are
  pinned by ``config/skills.lock`` (config governance G3);
- env-only sections (``vault``, ``auth``) stay out of every yaml file — they
  are injected via environment variables only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SPLIT_FILES = ("agent_legion.yaml",)
ENV_ONLY_SECTIONS = ("vault", "auth")


def _load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name} root must be a mapping"
    return data


def test_tracked_split_files_have_no_cms_section():
    for name in SPLIT_FILES:
        mapping = _load(name)
        assert "cms" not in mapping, (
            f"{name} carries the retired global cms: section; "
            "CMS defaults live in the capability config_schema, "
            "base_url/token arrive via env or workspace node config"
        )


def test_tracked_skill_safety_repos_are_path_only():
    openclaw = _load("agent_legion.yaml").get("openclaw") or {}
    repos = (openclaw.get("skill_safety") or {}).get("repos") or []
    for repo in repos:
        assert set(repo) <= {"path"}, (
            "skill_safety repos are a pure path allowlist (G3); "
            "refs resolve from config/skills.lock"
        )


def test_tracked_split_files_have_no_env_only_sections():
    for name in SPLIT_FILES:
        mapping = _load(name)
        present = [key for key in ENV_ONLY_SECTIONS if key in mapping]
        assert not present, f"{name} carries env-only sections: {present}"
