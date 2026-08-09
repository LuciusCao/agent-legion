from __future__ import annotations

import concurrent.futures
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from server.app.services.skill_source_store import InMemorySkillSourceStore
from server.app.skills.config import LockedSkillSource, SkillsConfig, SkillsLock
from server.app.skills.errors import SkillConfigError, SkillPathError, SkillRepoError
from server.app.skills.manager import SkillManager
from tests.helpers.skill_store import memory_skill_store


def test_skills_config_parses_minimal() -> None:
    data = {
        "skills": {
            "question_comprehension_info": {"repo": "https://example.com/skills.git", "ref": "main"}
        }
    }
    config = SkillsConfig.model_validate(data)
    assert config.skills["question_comprehension_info"].repo == "https://example.com/skills.git"
    assert config.skills["question_comprehension_info"].ref == "main"


def test_skills_config_parses_workflow_capability_key() -> None:
    data = {
        "skills": {
            "question_comprehension_info/generate_key_info": {
                "repo": "https://example.com/skills.git",
                "ref": "v1.2.3",
            }
        }
    }
    config = SkillsConfig.model_validate(data)
    key = "question_comprehension_info/generate_key_info"
    assert key in config.skills
    assert config.skills[key].repo == "https://example.com/skills.git"
    assert config.skills[key].ref == "v1.2.3"
    assert config.model_dump() == data


def test_skills_lock_parses_and_serializes() -> None:
    data = {
        "version": "1",
        "resolved_at": "2026-06-19T06:59:19Z",
        "skills": {
            "question_comprehension_info": {
                "repo": "https://example.com/skills.git",
                "ref": "main",
                "commit": "abc123def456",
            }
        },
    }
    lock = SkillsLock.model_validate(data)
    assert lock.version == "1"
    assert lock.resolved_at == "2026-06-19T06:59:19Z"
    assert lock.skills["question_comprehension_info"].repo == "https://example.com/skills.git"
    assert lock.skills["question_comprehension_info"].ref == "main"
    assert lock.skills["question_comprehension_info"].commit == "abc123def456"
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
    # When invoked from a git hook (e.g. pre-commit), these variables point at
    # the parent repository. They must not leak into the temporary test repos,
    # otherwise commands like `git -C <tmp> push origin HEAD` would operate on
    # the parent repo and push to its remote instead of the local bare repo.
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    return env


def _make_bare_repo(tmp_path: Path) -> str:
    env = _git_env()
    repo = tmp_path / "remote.git"
    repo.mkdir()
    # Pin the initial branch: fixtures reference `main`, but the default
    # branch of `git init` varies with the runner's git configuration (e.g.
    # CI defaults to `master`).
    subprocess.run(["git", "init", "--bare", "-b", "main", str(repo)], check=True, env=env)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", str(repo), str(work / "clone")], check=True, env=env)
    clone = work / "clone"
    (clone / "SKILL.md").write_text("# skill\n")
    (clone / "references").mkdir()
    (clone / "references" / "output-contract.md").write_text("contract\n")
    (clone / "scripts").mkdir()
    (clone / "scripts" / "validate_output.py").write_text("print('ok')\n")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD"], check=True, env=env)
    return f"file://{repo.resolve()}"


def _push_new_commit(repo_uri: str, tmp_path: Path, content: str) -> None:
    env = _git_env()
    work = tmp_path / "work" / "clone"
    (work / "SKILL.md").write_text(content)
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-m", "update", "--no-gpg-sign"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(work), "push", "origin", "HEAD"], check=True, env=env)


def _make_in_place_repo(repo: Path) -> str:
    env = _git_env()
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / "SKILL.md").write_text("# local skill\n")
    (repo / "references").mkdir()
    (repo / "references" / "output-contract.md").write_text("contract\n")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "validate_output.py").write_text("print('ok')\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        env=env,
    )
    subprocess.run(["git", "-C", str(repo), "tag", "v1.0.0"], check=True, env=env)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


_KEY = "question_comprehension_info/generate_key_info"


def _make_manager(
    tmp_path: Path,
    skills: dict[str, dict[str, str]],
) -> SkillManager:
    return SkillManager(
        store=memory_skill_store(skills),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )


def _single_skill(repo_uri: str, ref: str = "main") -> dict[str, dict[str, str]]:
    return {_KEY: {"repo": repo_uri, "ref": ref}}


def test_get_skill_dir_clones_and_returns_isolated_copy(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    manager = _make_manager(tmp_path, _single_skill(repo_uri))

    execution_id = str(uuid.uuid4())
    skill_dir = manager.get_skill_dir(_KEY, execution_id)

    assert skill_dir.is_dir()
    assert (
        skill_dir
        == tmp_path / "runs" / execution_id / "question_comprehension_info" / "generate_key_info"
    )
    assert (skill_dir / "SKILL.md").is_file()
    assert not (skill_dir / ".git").exists()


def _make_manager_with_cache(tmp_path: Path) -> SkillManager:
    repo_uri = _make_bare_repo(tmp_path)
    manager = _make_manager(tmp_path, _single_skill(repo_uri))
    manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    return manager


def test_cache_lock_files_live_under_runs_dir(tmp_path: Path) -> None:
    """Lock files are runtime state, not skill content (issue #42). filelock
    deletes its file on release, so the location is asserted while held."""
    manager = _make_manager_with_cache(tmp_path)
    cache_dir = tmp_path / "skills" / "question_comprehension_info" / "generate_key_info"

    with manager._cache_lock_for(cache_dir):
        held = sorted(path.name for path in (tmp_path / "runs" / ".locks").glob("*.lock"))
        assert held == ["question_comprehension_info--generate_key_info.lock"]
        assert list((tmp_path / "skills").rglob("*.lock")) == []


def test_get_skill_dir_works_with_read_only_skills_base(tmp_path: Path) -> None:
    """Docker :ro skills mount regression (issue #42): once the cache is
    cloned and pinned, resolving a skill must not write the skills base at
    all — locks live under runs_dir and a clean cache at the locked commit
    skips checkout/clean entirely."""
    manager = _make_manager_with_cache(tmp_path)
    base_dir = tmp_path / "skills"

    def _chmod(mode_dir: int, mode_file: int) -> None:
        for path in sorted(base_dir.rglob("*"), reverse=True):
            path.chmod(mode_dir if path.is_dir() else mode_file)

    _chmod(0o555, 0o444)
    try:
        skill_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))
        assert (skill_dir / "SKILL.md").is_file()
    finally:
        _chmod(0o755, 0o644)


def test_tilde_local_source_can_be_managed_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    base_dir = fake_home / ".agents" / "skills" / "agent-legion"
    repo = base_dir / "question_comprehension_info" / "generate_key_info"
    commit = _make_in_place_repo(repo)
    monkeypatch.setenv("HOME", str(fake_home))

    manager = SkillManager(
        store=memory_skill_store(
            {
                _KEY: {
                    "repo": (
                        "~/.agents/skills/agent-legion/"
                        "question_comprehension_info/generate_key_info"
                    ),
                    "ref": "v1.0.0",
                }
            }
        ),
        base_dir=base_dir,
        runs_dir=tmp_path / "runs",
    )

    skill_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))

    assert (skill_dir / "SKILL.md").read_text() == "# local skill\n"
    assert manager._load_lock().skills[_KEY].commit == commit
    assert (repo / ".git").is_dir()


def test_normalize_repo_expands_tilde_per_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    manager = _make_manager(tmp_path, {})
    assert manager._normalize_repo("~/.agents/skills/agent-legion/wf/cap") == str(
        (fake_home / ".agents" / "skills" / "agent-legion" / "wf" / "cap").resolve()
    )


@pytest.mark.parametrize(
    "repo",
    [
        "file:///nonexistent/repo.git",
        "https://example.com/skills.git",
        "git@example.com:skills.git",
    ],
)
def test_normalize_repo_leaves_urls_untouched(tmp_path: Path, repo: str) -> None:
    manager = _make_manager(tmp_path, {})
    assert manager._normalize_repo(repo) == repo


def test_normalize_repo_resolves_absolute_path(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path, {})
    assert manager._normalize_repo(str(tmp_path / "repo")) == str((tmp_path / "repo").resolve())


def test_lock_commit_used_even_when_ref_drifts(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    manager = _make_manager(tmp_path, _single_skill(repo_uri))

    first_execution = str(uuid.uuid4())
    first_dir = manager.get_skill_dir(_KEY, first_execution)
    locked_content = (first_dir / "SKILL.md").read_text()

    _push_new_commit(repo_uri, tmp_path, "# updated skill\n")

    second_execution = str(uuid.uuid4())
    second_dir = manager.get_skill_dir(_KEY, second_execution)

    assert (second_dir / "SKILL.md").read_text() == locked_content


def test_lock_source_drift_is_rejected(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    store = memory_skill_store(_single_skill(repo_uri))
    manager = SkillManager(
        store=store,
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    store.put_sources(SkillsConfig.model_validate({"skills": _single_skill(repo_uri, "HEAD")}))

    with pytest.raises(SkillConfigError, match="refresh"):
        manager.get_skill_dir(_KEY, str(uuid.uuid4()))


def test_refresh_lock_replaces_existing_pin(tmp_path: Path) -> None:
    from server.app.skills.lock import refresh_lock

    repo_uri = _make_bare_repo(tmp_path)
    store = memory_skill_store(_single_skill(repo_uri))
    base_dir = tmp_path / "skills"
    manager = SkillManager(
        store=store,
        base_dir=base_dir,
        runs_dir=tmp_path / "runs",
    )
    manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    first_commit = manager._load_lock().skills[_KEY].commit

    _push_new_commit(repo_uri, tmp_path, "# updated skill\n")
    refresh_lock(store, base_dir)

    refreshed = manager._load_lock().skills[_KEY]
    assert refreshed.commit != first_commit
    assert refreshed.repo == repo_uri
    assert refreshed.ref == "main"


def test_undeclared_skill_key_raises_config_error(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path, {})
    with pytest.raises(SkillConfigError):
        manager.get_skill_dir("not/declared", str(uuid.uuid4()))


def test_isolated_copies_do_not_interfere(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    manager = _make_manager(tmp_path, _single_skill(repo_uri))

    first_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    second_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))

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
    manager = _make_manager(tmp_path, {})
    with pytest.raises((SkillPathError, SkillConfigError)):
        manager.get_skill_dir(skill_key, str(uuid.uuid4()))


def test_lock_refresh_command_writes_lock(tmp_path: Path) -> None:
    from server.app.skills.lock import refresh_lock

    repo_uri = _make_bare_repo(tmp_path)
    store = memory_skill_store(_single_skill(repo_uri, "HEAD"))

    refresh_lock(store, tmp_path / "skills")

    lock = store.get_lock()
    assert lock is not None
    entry = lock.skills[_KEY]
    assert entry.repo == repo_uri
    assert entry.commit


def test_corrupted_cache_is_repaired_to_clean_copy(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    manager = _make_manager(tmp_path, _single_skill(repo_uri))

    first_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    assert (first_dir / "SKILL.md").is_file()

    cache_dir = tmp_path / "skills" / "question_comprehension_info" / "generate_key_info"
    (cache_dir / "garbage.txt").write_text("trash")

    second_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    assert second_dir.is_dir()
    assert not (second_dir / "garbage.txt").exists()
    assert (second_dir / "SKILL.md").is_file()


def test_concurrent_get_skill_dir_serializes_git_operations(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    manager = _make_manager(tmp_path, _single_skill(repo_uri))

    def fetch_copy(_index: int) -> Path:
        return manager.get_skill_dir(_KEY, str(uuid.uuid4()))

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
    manager = _make_manager(tmp_path, _single_skill(repo_uri))

    cache_dir = tmp_path / "skills" / "question_comprehension_info" / "generate_key_info"
    cache_dir.mkdir(parents=True)

    with pytest.raises(SkillRepoError):
        manager.get_skill_dir(_KEY, str(uuid.uuid4()))


def test_lock_content_stays_stable_across_calls(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    manager = _make_manager(tmp_path, _single_skill(repo_uri))

    manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    first_lock = manager._load_lock()
    first_commit = first_lock.skills[_KEY].commit

    manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    second_lock = manager._load_lock()

    assert second_lock.skills[_KEY].commit == first_commit
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
    manager = _make_manager(tmp_path, {})
    with pytest.raises(SkillPathError):
        manager.get_skill_dir(_KEY, execution_id)


def test_concurrent_two_skills_lock_retains_both_commits(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    dir_b = tmp_path / "b"
    dir_b.mkdir()
    repo_a = _make_bare_repo(dir_a)
    repo_b = _make_bare_repo(dir_b)
    _push_new_commit(repo_a, dir_a, "# skill a\n")
    _push_new_commit(repo_b, dir_b, "# skill b\n")
    manager = _make_manager(
        tmp_path,
        {
            _KEY: {"repo": repo_a, "ref": "main"},
            "summarization/summarize": {"repo": repo_b, "ref": "main"},
        },
    )

    def fetch(key: str) -> Path:
        return manager.get_skill_dir(key, str(uuid.uuid4()))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(fetch, key)
            for key in ("question_comprehension_info/generate_key_info", "summarization/summarize")
        ]
        dirs = [f.result() for f in concurrent.futures.as_completed(futures)]

    lock = manager._load_lock()
    assert "question_comprehension_info/generate_key_info" in lock.skills
    assert "summarization/summarize" in lock.skills
    assert lock.skills["question_comprehension_info/generate_key_info"].commit
    assert lock.skills["summarization/summarize"].commit
    assert (
        lock.skills["question_comprehension_info/generate_key_info"].commit
        != lock.skills["summarization/summarize"].commit
    )
    assert len({str(d) for d in dirs}) == 2
    for skill_dir in dirs:
        assert (skill_dir / "SKILL.md").is_file()
        assert not (skill_dir / ".git").exists()


def test_invalid_repo_uri_raises_skill_repo_error(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path, _single_skill("file:///nonexistent/repo.git"))
    with pytest.raises(SkillRepoError):
        manager.get_skill_dir(_KEY, str(uuid.uuid4()))


def test_missing_sources_document_behaves_as_empty(tmp_path: Path) -> None:
    """An unseeded store (None document) is an empty config, not an error."""
    manager = SkillManager(
        store=InMemorySkillSourceStore(),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    assert manager._load_config().skills == {}
    assert manager.load_lock().skills == {}
    with pytest.raises(SkillConfigError, match="DB skill sources"):
        manager.get_skill_dir(_KEY, str(uuid.uuid4()))
