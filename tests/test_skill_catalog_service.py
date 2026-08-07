from pathlib import Path

import pytest

from server.app.services.job_errors import NotFoundError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.config import SkillsConfig, SkillsLock
from tests.postgres_support import TEST_DATABASE_URL


def _put_document(sources: dict, lock: dict | None = None) -> None:
    store = SkillSourceStore(TEST_DATABASE_URL)
    store.put_sources(SkillsConfig.model_validate({"skills": sources}))
    store.put_lock(SkillsLock.model_validate(lock or {}))


def test_skill_detail_lists_safe_text_files_and_locked_version(tmp_path: Path) -> None:
    _put_document(
        {"demo/review": {"repo": "local", "ref": "v1.2.0"}},
        {"skills": {"demo/review": {"repo": "local", "ref": "v1.2.0", "commit": "abc123"}}},
    )
    base = tmp_path / "skills"
    skill = base / "demo" / "review"
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text("# Review\n")
    (skill / "references" / "rules.md").write_text("rules\n")
    (skill / "scripts" / "validate.py").write_text("print('ok')\n")
    (skill / "scripts" / "ignored.bin").write_bytes(b"binary")

    detail = SkillCatalogService(TEST_DATABASE_URL, base).detail("demo/review")

    assert detail["ref"] == "v1.2.0"
    assert detail["commit"] == "abc123"
    assert [item["path"] for item in detail["files"]] == [
        "SKILL.md",
        "references/rules.md",
        "scripts/validate.py",
    ]


def test_skill_detail_rejects_unconfigured_keys(tmp_path: Path) -> None:
    _put_document({})

    with pytest.raises(NotFoundError):
        SkillCatalogService(TEST_DATABASE_URL).detail("../secret")
