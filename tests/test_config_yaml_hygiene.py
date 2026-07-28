"""CONFIG-YAML-001 evidence: tracked config yaml files carry no secret values.

Scans the tracked split configuration files and asserts the hygiene red lines
hold in the repository itself (the startup-side rejections are covered by
tests/test_settings.py and tests/test_configuration_loader.py):

- retired secret keys (``cms.token`` / ``cms.token_gen``, config governance G2)
  never reappear — tokens live in env or the vault;
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
SPLIT_FILES = ("app.yaml", "agent_legion.yaml", "workflow.yaml")
ENV_ONLY_SECTIONS = ("vault", "auth")


def _load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name} root must be a mapping"
    return data


def test_tracked_cms_section_has_no_retired_secret_keys():
    cms = _load("agent_legion.yaml").get("cms") or {}
    assert "token" not in cms, "cms.token was retired (G2); use env or a vault binding"
    assert "token_gen" not in cms, "cms.token_gen was retired (G2); use BASECMS_* env"


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
