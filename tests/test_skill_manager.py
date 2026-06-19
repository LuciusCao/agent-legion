from __future__ import annotations

import concurrent.futures
import os
import subprocess
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


def _git_env() -> dict[str, str]:
    env = {**dict(os.environ)}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    return env


def _make_bare_repo(tmp_path: Path) -> str:
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
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        env=_git_env(),
    )
    subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD"], check=True)
    return f"file://{repo.resolve()}"


def _push_new_commit(repo_uri: str, tmp_path: Path, content: str) -> None:
    work = tmp_path / "work" / "clone"
    (work / "SKILL.md").write_text(content)
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-m", "update", "--no-gpg-sign"],
        check=True,
        env=_git_env(),
    )
    subprocess.run(["git", "-C", str(work), "push", "origin", "HEAD"], check=True)


def test_get_skill_dir_clones_and_returns_isolated_copy(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  reading_analysis/extract_keywords:\n    repo: {repo_uri}\n    ref: main\n"
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
    assert skill_dir == tmp_path / "runs" / execution_id / "reading_analysis" / "extract_keywords"
    assert (skill_dir / "SKILL.md").is_file()
    assert not (skill_dir / ".git").exists()


def test_lock_commit_used_even_when_ref_drifts(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  reading_analysis/extract_keywords:\n    repo: {repo_uri}\n    ref: main\n"
    )
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )

    first_execution = str(uuid.uuid4())
    first_dir = manager.get_skill_dir("reading_analysis/extract_keywords", first_execution)
    locked_content = (first_dir / "SKILL.md").read_text()

    _push_new_commit(repo_uri, tmp_path, "# updated skill\n")

    second_execution = str(uuid.uuid4())
    second_dir = manager.get_skill_dir("reading_analysis/extract_keywords", second_execution)

    assert (second_dir / "SKILL.md").read_text() == locked_content


def test_undeclared_skill_key_raises_config_error(tmp_path: Path) -> None:
    config_path = tmp_path / "skills.yaml"
    config_path.write_text("skills: {}\n")
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    with pytest.raises(SkillConfigError):
        manager.get_skill_dir("not/declared", str(uuid.uuid4()))


def test_isolated_copies_do_not_interfere(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  reading_analysis/extract_keywords:\n    repo: {repo_uri}\n    ref: main\n"
    )
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )

    first_dir = manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))
    second_dir = manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))

    original = (first_dir / "SKILL.md").read_text()
    (first_dir / "SKILL.md").write_text("# modified\n")

    assert (second_dir / "SKILL.md").read_text() == original


@pytest.mark.parametrize(
    "skill_key",
    [
        "../escape",
        "foo/../bar",
        "/absolute/key",
        "",
        "no-slash",
        "foo//bar",
        "foo/bar/baz",
        "foo/",
        "/foo",
    ],
)
def test_malicious_or_absolute_or_empty_skill_key_rejected(skill_key: str, tmp_path: Path) -> None:
    config_path = tmp_path / "skills.yaml"
    config_path.write_text("skills: {}\n")
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    with pytest.raises((SkillPathError, SkillConfigError)):
        manager.get_skill_dir(skill_key, str(uuid.uuid4()))


def test_lock_refresh_command_writes_lock(tmp_path: Path) -> None:
    from server.app.skills.lock import refresh_lock

    repo_uri = _make_bare_repo(tmp_path)
    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  reading_analysis/extract_keywords:\n    repo: {repo_uri}\n    ref: HEAD\n"
    )
    lock_path = tmp_path / "skills.lock"
    base_dir = tmp_path / "skills"

    refresh_lock(config_path, lock_path, base_dir)

    assert lock_path.is_file()
    content = lock_path.read_text()
    assert "reading_analysis/extract_keywords" in content
    assert "commit:" in content


def test_corrupted_cache_is_repaired_to_clean_copy(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  reading_analysis/extract_keywords:\n    repo: {repo_uri}\n    ref: main\n"
    )
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )

    first_dir = manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))
    assert (first_dir / "SKILL.md").is_file()

    cache_dir = tmp_path / "skills" / "reading_analysis" / "extract_keywords"
    (cache_dir / "garbage.txt").write_text("trash")

    second_dir = manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))
    assert second_dir.is_dir()
    assert not (second_dir / "garbage.txt").exists()
    assert (second_dir / "SKILL.md").is_file()


def test_concurrent_get_skill_dir_serializes_git_operations(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  reading_analysis/extract_keywords:\n    repo: {repo_uri}\n    ref: main\n"
    )
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )

    def fetch_copy(_index: int) -> Path:
        return manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_copy, i) for i in range(5)]
        dirs = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len({str(d) for d in dirs}) == 5
    for skill_dir in dirs:
        assert skill_dir.is_dir()
        assert (skill_dir / "SKILL.md").is_file()
        assert not (skill_dir / ".git").exists()


def test_broken_cache_directory_raises_skill_repo_error(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  reading_analysis/extract_keywords:\n    repo: {repo_uri}\n    ref: main\n"
    )
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )

    cache_dir = tmp_path / "skills" / "reading_analysis" / "extract_keywords"
    cache_dir.mkdir(parents=True)

    with pytest.raises(SkillRepoError):
        manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))


def test_lockfile_content_stays_stable_across_calls(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    config_path = tmp_path / "skills.yaml"
    config_path.write_text(
        f"skills:\n  reading_analysis/extract_keywords:\n    repo: {repo_uri}\n    ref: main\n"
    )
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )

    manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))
    first_lock = manager._load_lock()
    first_commit = first_lock.skills["reading_analysis/extract_keywords"].commit

    manager.get_skill_dir("reading_analysis/extract_keywords", str(uuid.uuid4()))
    second_lock = manager._load_lock()

    assert second_lock.skills["reading_analysis/extract_keywords"].commit == first_commit
    assert second_lock.resolved_at == first_lock.resolved_at


@pytest.mark.parametrize(
    "execution_id",
    [
        "",
        "..",
        "/absolute/id",
        "foo/bar",
        "foo\\bar",
        "foo..bar",
        "foo bar",
        "foo?bar",
        "foo:bar",
    ],
)
def test_invalid_execution_id_rejected(execution_id: str, tmp_path: Path) -> None:
    config_path = tmp_path / "skills.yaml"
    config_path.write_text("skills: {}\n")
    manager = SkillManager(
        config_path=config_path,
        lock_path=tmp_path / "skills.lock",
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    with pytest.raises(SkillPathError):
        manager.get_skill_dir("reading_analysis/extract_keywords", execution_id)
