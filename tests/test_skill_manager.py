from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from server.app.skills.config import LockedSkillSource, SkillsConfig, SkillsLock
from server.app.skills.errors import SkillConfigError, SkillPathError, SkillRepoError
from server.app.skills.manager import SkillManager


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


def test_skill_config_error_is_value_error() -> None:
    with pytest.raises(ValueError):
        raise SkillConfigError("bad config")


def test_skill_repo_error_is_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        raise SkillRepoError("git failed")


def test_skill_path_error_is_value_error() -> None:
    with pytest.raises(ValueError):
        raise SkillPathError("path escape")


def _make_bare_repo(tmp_path: Path) -> str:
    import subprocess

    repo = tmp_path / "remote.git"
    repo.mkdir()
    subprocess.run(["git", "init", "--bare", str(repo)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", str(repo), str(work / "clone")], check=True)
    clone = work / "clone"
    (clone / "SKILL.md").write_text("# skill\n")
    (clone / "references").mkdir()
    (clone / "references" / "output-contract.md").write_text("contract\n")
    (clone / "scripts").mkdir()
    (clone / "scripts" / "validate_output.py").write_text("print('ok')\n")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
    env = {**dict(__import__("os").environ)}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD"], check=True)
    return f"file://{repo.resolve()}"


def test_get_skill_dir_clones_and_returns_isolated_copy(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  reading_analysis/extract_keywords:\n    repo: {repo_uri}\n    ref: HEAD\n"
    )
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )

    execution_id = str(uuid.uuid4())
    skill_dir = manager.get_skill_dir("reading_analysis/extract_keywords", execution_id)

    assert skill_dir.is_dir()
    assert (skill_dir / "SKILL.md").is_file()
    assert "runs" in str(skill_dir)
