from pathlib import Path

import pytest

from server.app.services.job_errors import NotFoundError
from server.app.services.skill_catalog import SkillCatalogService


def test_skill_detail_lists_safe_text_files_and_locked_version(tmp_path: Path) -> None:
    root = tmp_path / "project"
    base = tmp_path / "skills"
    (root / "config").mkdir(parents=True)
    (root / "config" / "skills.yaml").write_text(
        "skills:\n  demo/review:\n    repo: local\n    ref: v1.2.0\n"
    )
    (root / "config" / "skills.lock").write_text(
        "version: '1'\nskills:\n  demo/review:\n    repo: local\n    ref: v1.2.0\n    commit: abc123\n"
    )
    skill = base / "demo" / "review"
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text("# Review\n")
    (skill / "references" / "rules.md").write_text("rules\n")
    (skill / "scripts" / "validate.py").write_text("print('ok')\n")
    (skill / "scripts" / "ignored.bin").write_bytes(b"binary")

    detail = SkillCatalogService(root, base).detail("demo/review")

    assert detail["ref"] == "v1.2.0"
    assert detail["commit"] == "abc123"
    assert [item["path"] for item in detail["files"]] == [
        "SKILL.md",
        "references/rules.md",
        "scripts/validate.py",
    ]


def test_skill_detail_rejects_unconfigured_keys(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "skills.yaml").write_text("skills: {}\n")

    with pytest.raises(NotFoundError):
        SkillCatalogService(tmp_path).detail("../secret")
