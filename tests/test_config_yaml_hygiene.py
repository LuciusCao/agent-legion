"""CONFIG-YAML-001 evidence: tracked config yaml files carry no secret values.

All runtime split configuration files are retired (``app.yaml`` /
``workflow.yaml`` / ``agent_legion.yaml`` fail startup when present, with
migration guidance), so this suite scans the remaining tracked config yaml
files and asserts the hygiene red lines hold in the repository itself (the
startup-side rejections are covered by tests/test_settings.py and
tests/test_configuration_loader.py):

- the global ``cms:`` section stays retired — CMS defaults live in the
  capability ``config_schema``; endpoint/credentials live on the
  instance-level external connection (retired ``cms.token`` /
  ``cms.token_gen``, config governance G2, can never reappear);
- the ``asr:`` section stays retired — business parameters live in the
  transcribe_video capability ``config_schema``; machine paths arrive via the
  ``AGENT_LEGION_ASR_*`` env overrides only;
- ``openclaw.skill_safety.repos`` stay a pure path allowlist — the yaml
  ``openclaw:`` section retired into the DB instance settings document, so
  the code defaults in ``configuration/instance_defaults.py`` are the
  tracked source; refs are pinned by the DB ``skill_lock`` document (config
  governance G3);
- env-only sections (``vault``, ``auth``) stay out of every yaml file — they
  are injected via environment variables only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from server.app.configuration.openclaw_defaults import DEFAULT_OPENCLAW_CONFIG

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
TRACKED_CONFIG_FILES = ("agent-worker.example.yaml",)
ENV_ONLY_SECTIONS = ("vault", "auth")


def _load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name} root must be a mapping"
    return data


def test_tracked_config_files_have_no_cms_section():
    for name in TRACKED_CONFIG_FILES:
        mapping = _load(name)
        assert "cms" not in mapping, (
            f"{name} carries the retired global cms: section; "
            "CMS defaults live in the capability config_schema, "
            "endpoint/credentials live on the instance-level external connection"
        )


def test_tracked_config_files_have_no_asr_section():
    for name in TRACKED_CONFIG_FILES:
        mapping = _load(name)
        assert "asr" not in mapping, (
            f"{name} carries the retired global asr: section; "
            "business parameters live in the transcribe_video capability "
            "config_schema, machine paths arrive via AGENT_LEGION_ASR_* env"
        )


def test_skill_safety_repos_are_path_only():
    repos = DEFAULT_OPENCLAW_CONFIG["skill_safety"]["repos"]
    assert repos, "code defaults must keep the retired skill_safety whitelist"
    for repo in repos:
        assert set(repo) <= {"path"}, (
            "skill_safety repos are a pure path allowlist (G3); "
            "refs resolve from the DB skill_lock document"
        )


def test_tracked_config_files_have_no_env_only_sections():
    for name in TRACKED_CONFIG_FILES:
        mapping = _load(name)
        present = [key for key in ENV_ONLY_SECTIONS if key in mapping]
        assert not present, f"{name} carries env-only sections: {present}"
