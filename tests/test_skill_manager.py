from __future__ import annotations

import pytest

from server.app.skills.config import LockedSkillSource, SkillsConfig, SkillsLock
from server.app.skills.errors import SkillConfigError, SkillPathError, SkillRepoError


def test_skills_config_parses_minimal() -> None:
    data = {
        "skills": {"reading_analysis": {"repo": "https://example.com/skills.git", "ref": "main"}}
    }
    config = SkillsConfig.model_validate(data)
    assert config.skills["reading_analysis"].repo == "https://example.com/skills.git"
    assert config.skills["reading_analysis"].ref == "main"


def test_skills_config_parses_workflow_capability_key() -> None:
    data = {
        "skills": {
            "reading_analysis/extract_keywords": {
                "repo": "https://example.com/skills.git",
                "ref": "v1.2.3",
            }
        }
    }
    config = SkillsConfig.model_validate(data)
    key = "reading_analysis/extract_keywords"
    assert key in config.skills
    assert config.skills[key].repo == "https://example.com/skills.git"
    assert config.skills[key].ref == "v1.2.3"
    assert config.model_dump() == data


def test_skills_lock_parses_and_serializes() -> None:
    data = {
        "version": "1",
        "resolved_at": "2026-06-19T06:59:19Z",
        "skills": {
            "reading_analysis": {
                "repo": "https://example.com/skills.git",
                "ref": "main",
                "commit": "abc123def456",
            }
        },
    }
    lock = SkillsLock.model_validate(data)
    assert lock.version == "1"
    assert lock.resolved_at == "2026-06-19T06:59:19Z"
    assert lock.skills["reading_analysis"].repo == "https://example.com/skills.git"
    assert lock.skills["reading_analysis"].ref == "main"
    assert lock.skills["reading_analysis"].commit == "abc123def456"
    assert lock.model_dump() == data


def test_skills_lock_defaults() -> None:
    lock = SkillsLock()
    assert lock.version == "1"
    assert lock.resolved_at is None
    assert lock.skills == {}


def test_locked_skill_source_round_trip() -> None:
    source = LockedSkillSource(repo="https://example.com/skills.git", ref="main", commit="abc123")
    assert source.model_dump() == {
        "repo": "https://example.com/skills.git",
        "ref": "main",
        "commit": "abc123",
    }


@pytest.mark.parametrize("exc_cls", [SkillConfigError, SkillRepoError, SkillPathError])
def test_skill_exceptions_can_be_raised_and_caught(exc_cls: type[Exception]) -> None:
    with pytest.raises(exc_cls):
        raise exc_cls("test error")


def test_skill_config_error_is_value_error() -> None:
    with pytest.raises(ValueError):
        raise SkillConfigError("bad config")


def test_skill_repo_error_is_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        raise SkillRepoError("git failed")


def test_skill_path_error_is_value_error() -> None:
    with pytest.raises(ValueError):
        raise SkillPathError("path escape")
