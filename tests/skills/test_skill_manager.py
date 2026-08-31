from __future__ import annotations

import concurrent.futures
import stat
import uuid
from pathlib import Path

import pytest

from server.app.skills.config import LATEST_REF, LockedSkill, SkillsLock
from server.app.skills.errors import SkillPathError, SkillRepoError
from server.app.skills.manager import SkillManager
from tests.helpers.skill_git import (
    _KEY,
    _commit_skill_update,
    _head_commit,
    _make_manager,
    _make_skill_repo,
    _tag,
)
from tests.helpers.skill_store import memory_skill_store

pytestmark = pytest.mark.no_db


def test_skills_lock_parses_and_serializes() -> None:
    data = {
        "version": "2",
        "resolved_at": "2026-06-19T06:59:19Z",
        "skills": {
            "demo_workflow": {
                "repo": "https://example.com/skills.git",
                "refs": {"main": "abc123def456"},
            }
        },
    }
    lock = SkillsLock.model_validate(data)
    assert lock.version == "2"
    assert lock.resolved_at == "2026-06-19T06:59:19Z"
    assert lock.skills["demo_workflow"].repo == "https://example.com/skills.git"
    assert lock.skills["demo_workflow"].refs == {"main": "abc123def456"}
    assert lock.model_dump() == data


def test_skills_lock_upgrades_v1_entries() -> None:
    """v1 lock documents ({repo, ref, commit} per skill) upgrade to the
    multi-ref shape on read; the version label is re-stamped v2 (issue #76)."""
    data = {
        "version": "1",
        "skills": {
            "demo_workflow": {
                "repo": "https://example.com/skills.git",
                "ref": "main",
                "commit": "abc123def456",
            }
        },
    }
    lock = SkillsLock.model_validate(data)
    assert lock.version == "2"
    entry = lock.skills["demo_workflow"]
    assert entry.repo == "https://example.com/skills.git"
    assert entry.refs == {"main": "abc123def456"}


def test_skills_lock_tolerates_missing_version_field() -> None:
    lock = SkillsLock.model_validate(
        {"skills": {"wf/cap": {"repo": "r", "ref": "v1", "commit": "abc123"}}}
    )
    assert lock.version == "2"
    assert lock.skills["wf/cap"].refs == {"v1": "abc123"}


def test_skills_lock_defaults() -> None:
    lock = SkillsLock()
    assert lock.version == "2"
    assert lock.resolved_at is None
    assert lock.skills == {}


def test_locked_skill_round_trip() -> None:
    locked = LockedSkill(repo="https://example.com/skills.git", refs={"main": "abc123"})
    assert locked.model_dump() == {
        "repo": "https://example.com/skills.git",
        "refs": {"main": "abc123"},
    }


def test_skill_repo_error_is_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        raise SkillRepoError("git failed")


def test_skill_path_error_is_value_error() -> None:
    with pytest.raises(ValueError):
        raise SkillPathError("path escape")


def test_get_skill_dir_returns_isolated_copy(tmp_path: Path) -> None:
    _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)

    execution_id = str(uuid.uuid4())
    skill_dir = manager.get_skill_dir(_KEY, execution_id)

    assert skill_dir.is_dir()
    assert skill_dir == tmp_path / "runs" / execution_id / "demo_workflow" / "generate_key_info"
    assert (skill_dir / "SKILL.md").is_file()
    assert not (skill_dir / ".git").exists()


def _make_manager_with_cache(tmp_path: Path) -> SkillManager:
    _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)
    manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    return manager


def test_cache_lock_files_live_under_runs_dir(tmp_path: Path) -> None:
    """Lock files are runtime state, not skill content (issue #42). filelock
    deletes its file on release, so the location is asserted while held."""
    manager = _make_manager_with_cache(tmp_path)
    cache_dir = tmp_path / "skills" / "demo_workflow" / "generate_key_info"

    with manager._cache_lock_for(cache_dir):
        lock_dir = tmp_path / "runs" / ".locks"
        held = sorted(path.name for path in lock_dir.glob("*.lock"))
        assert held == ["demo_workflow--generate_key_info.lock"]
        assert list((tmp_path / "skills").rglob("*.lock")) == []
        # The lock dir is private to the service user (shared-temp hygiene).
        assert stat.S_IMODE(lock_dir.stat().st_mode) == 0o700


def test_get_skill_dir_works_with_read_only_skills_base(tmp_path: Path) -> None:
    """Docker :ro skills mount regression (issue #42): with the in-place repo
    already at the dispatch commit, resolving a skill must not write the
    skills base at all — locks live under runs_dir and a clean cache at the
    resolved commit skips checkout/clean entirely."""
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


def test_empty_ref_normalizes_to_latest_and_follows_head(tmp_path: Path) -> None:
    """#322: an empty ref is ``latest`` — every dispatch rev-parses the repo's
    live HEAD, so an updated skill is picked up WITHOUT any relock, and the
    version string records latest@commit12."""
    repo = _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)

    _, first_commit, first_version = manager.checkout_skill(_KEY, str(uuid.uuid4()))
    assert first_version == f"{LATEST_REF}@{first_commit[:12]}"

    new_commit = _commit_skill_update(repo, "# updated skill\n")
    run_dir, commit, version = manager.checkout_skill(_KEY, str(uuid.uuid4()))

    assert commit == new_commit
    assert version == f"{LATEST_REF}@{new_commit[:12]}"
    assert (run_dir / "SKILL.md").read_text() == "# updated skill\n"


def test_explicit_latest_matches_empty_ref_and_never_locks(tmp_path: Path) -> None:
    """Explicit ``latest`` is the same live-HEAD semantics as an empty ref,
    and neither ever reads or writes the lock (#322)."""
    repo = _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)
    head = _head_commit(repo)

    _, empty_commit, empty_version = manager.checkout_skill(_KEY, str(uuid.uuid4()))
    _, latest_commit, latest_version = manager.checkout_skill(
        _KEY, str(uuid.uuid4()), ref=LATEST_REF
    )

    assert empty_commit == latest_commit == head
    assert empty_version == latest_version == f"{LATEST_REF}@{head[:12]}"
    # The lock is never touched by latest: no entry, no "latest" pin.
    assert manager.load_lock().skills == {}


def test_pinned_tag_locks_on_first_dispatch(tmp_path: Path) -> None:
    """An explicit tag ref auto-locks its commit on first dispatch (#76),
    recording the in-place repo path as the audit-only repo field (#322)."""
    repo = _make_skill_repo(tmp_path / "skills")
    commit = _tag(repo, "v1.0.0")
    manager = _make_manager(tmp_path)

    skill_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1.0.0")

    assert (skill_dir / "SKILL.md").read_text() == "# skill\n"
    locked = manager.load_lock().skills[_KEY]
    assert locked.refs == {"v1.0.0": commit}
    assert locked.repo == str(repo.resolve())


def test_pinned_tag_survives_retag(tmp_path: Path) -> None:
    """A frozen tag pin does not drift: re-pointing the tag at a newer commit
    keeps dispatches on the locked commit until a relock."""
    repo = _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)

    locked_commit = _tag(repo, "v1.0.0")
    first_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1.0.0")
    assert (first_dir / "SKILL.md").read_text() == "# skill\n"

    _commit_skill_update(repo, "# updated skill\n")
    _tag(repo, "v1.0.0", force=True)

    second_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1.0.0")
    assert (second_dir / "SKILL.md").read_text() == "# skill\n"
    assert manager.load_lock().skills[_KEY].refs["v1.0.0"] == locked_commit


def test_refresh_lock_replaces_existing_pin(tmp_path: Path) -> None:
    from server.app.skills.lock import refresh_lock

    repo = _make_skill_repo(tmp_path / "skills")
    store = memory_skill_store()
    base_dir = tmp_path / "skills"
    manager = SkillManager(
        store=store,
        base_dir=base_dir,
        runs_dir=tmp_path / "runs",
        # No doc cache: refresh_lock writes through a separate instance, and
        # this test asserts the new pin is read back immediately.
        doc_cache_ttl_seconds=0,
    )
    _tag(repo, "v1.0.0")
    manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1.0.0")
    first_commit = manager.load_lock().skills[_KEY].refs["v1.0.0"]

    _commit_skill_update(repo, "# updated skill\n")
    _tag(repo, "v1.0.0", force=True)
    refresh_lock(store, base_dir)

    refreshed = manager.load_lock().skills[_KEY]
    assert refreshed.refs["v1.0.0"] != first_commit
    assert refreshed.repo == str(repo.resolve())


def test_missing_skill_repo_raises_with_guidance(tmp_path: Path) -> None:
    """#322: no re-clone self-heal — a missing in-place repo fails with
    guidance pointing at the skills root layout."""
    manager = _make_manager(tmp_path)
    with pytest.raises(SkillRepoError, match="in-place git repository"):
        manager.get_skill_dir("not/declared", str(uuid.uuid4()))


def test_isolated_copies_do_not_interfere(tmp_path: Path) -> None:
    _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)

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
    manager = _make_manager(tmp_path)
    with pytest.raises(SkillPathError):
        manager.get_skill_dir(skill_key, str(uuid.uuid4()))


def test_lock_refresh_command_writes_lock(tmp_path: Path) -> None:
    from server.app.skills.lock import refresh_lock

    repo = _make_skill_repo(tmp_path / "skills")
    commit = _tag(repo, "v1.0.0")
    store = memory_skill_store(
        lock={"skills": {_KEY: {"repo": "stale", "refs": {"v1.0.0": "0" * 40}}}}
    )

    refresh_lock(store, tmp_path / "skills")

    lock = store.get_lock()
    assert lock is not None
    entry = lock.skills[_KEY]
    assert entry.refs["v1.0.0"] == commit
    assert entry.repo == str(repo.resolve())


def test_lock_repo_field_is_audit_only(tmp_path: Path) -> None:
    """#322: the retired repo-drift gate is gone — a lock entry whose repo
    string does not match the in-place location still resolves its pin."""
    repo = _make_skill_repo(tmp_path / "skills")
    commit = _tag(repo, "v1.0.0")
    manager = _make_manager(
        tmp_path,
        lock={
            "skills": {_KEY: {"repo": "https://old.example.com/x.git", "refs": {"v1.0.0": commit}}}
        },
    )

    skill_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1.0.0")

    assert (skill_dir / "SKILL.md").is_file()


def test_corrupted_cache_is_repaired_to_clean_copy(tmp_path: Path) -> None:
    # 无 memo（PR 317 codex P1）：每次 checkout 都在锁内重探测，脏 cache 下一次
    # 即修复（见 test_dirty_cache_repaired_on_next_checkout）。
    _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)

    first_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    assert (first_dir / "SKILL.md").is_file()

    cache_dir = tmp_path / "skills" / "demo_workflow" / "generate_key_info"
    (cache_dir / "garbage.txt").write_text("trash")

    second_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    assert second_dir.is_dir()
    assert not (second_dir / "garbage.txt").exists()
    assert (second_dir / "SKILL.md").is_file()


def test_concurrent_get_skill_dir_serializes_git_operations(tmp_path: Path) -> None:
    _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)

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
    manager = _make_manager(tmp_path)

    cache_dir = tmp_path / "skills" / "demo_workflow" / "generate_key_info"
    cache_dir.mkdir(parents=True)

    with pytest.raises(SkillRepoError):
        manager.get_skill_dir(_KEY, str(uuid.uuid4()))


def test_lock_content_stays_stable_across_calls(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills")
    _tag(repo, "v1.0.0")
    manager = _make_manager(tmp_path)

    manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1.0.0")
    first_lock = manager.load_lock()
    first_commit = first_lock.skills[_KEY].refs["v1.0.0"]

    manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1.0.0")
    second_lock = manager.load_lock()

    assert second_lock.skills[_KEY].refs["v1.0.0"] == first_commit
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
    manager = _make_manager(tmp_path)
    with pytest.raises(SkillPathError):
        manager.get_skill_dir(_KEY, execution_id)


def test_concurrent_two_skills_lock_retains_both_commits(tmp_path: Path) -> None:
    repo_a = _make_skill_repo(tmp_path / "skills", _KEY, content="# skill a\n")
    repo_b = _make_skill_repo(tmp_path / "skills", "summarization/summarize", content="# skill b\n")
    _tag(repo_a, "v1.0.0")
    _tag(repo_b, "v1.0.0")
    manager = _make_manager(tmp_path)

    def fetch(key: str) -> Path:
        return manager.get_skill_dir(key, str(uuid.uuid4()), ref="v1.0.0")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(fetch, key)
            for key in ("demo_workflow/generate_key_info", "summarization/summarize")
        ]
        dirs = [f.result() for f in concurrent.futures.as_completed(futures)]

    lock = manager.load_lock()
    assert "demo_workflow/generate_key_info" in lock.skills
    assert "summarization/summarize" in lock.skills
    assert lock.skills["demo_workflow/generate_key_info"].refs["v1.0.0"]
    assert lock.skills["summarization/summarize"].refs["v1.0.0"]
    assert (
        lock.skills["demo_workflow/generate_key_info"].refs["v1.0.0"]
        != lock.skills["summarization/summarize"].refs["v1.0.0"]
    )
    assert len({str(d) for d in dirs}) == 2
    for skill_dir in dirs:
        assert (skill_dir / "SKILL.md").is_file()
        assert not (skill_dir / ".git").exists()


def test_missing_lock_document_behaves_as_empty(tmp_path: Path) -> None:
    """An unseeded store (None document) is an empty lock, not an error."""
    _make_skill_repo(tmp_path / "skills")
    store = memory_skill_store()
    manager = SkillManager(
        store=store,
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )
    assert manager.load_lock().skills == {}
    # latest never materializes a lock document.
    manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    assert store.get_lock() is None


class _CountingSkillStore:
    """SkillStore wrapper that counts lock document reads/writes."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.get_lock_calls = 0

    def get_lock(self) -> SkillsLock | None:
        self.get_lock_calls += 1
        return self._inner.get_lock()

    def put_lock(self, lock: SkillsLock) -> None:
        self._inner.put_lock(lock)


def test_lock_doc_is_cached_within_ttl(tmp_path: Path) -> None:
    store = _CountingSkillStore(memory_skill_store())
    manager = SkillManager(
        store=store,
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )

    manager._load_lock()
    manager._load_lock()

    assert store.get_lock_calls == 1


def test_doc_cache_expires_after_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1_000.0]
    monkeypatch.setattr("server.app.skills.doc_cache.time.monotonic", lambda: clock[0])
    store = _CountingSkillStore(memory_skill_store())
    manager = SkillManager(
        store=store,
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
        doc_cache_ttl_seconds=5.0,
    )

    manager._load_lock()
    clock[0] += 4.9
    manager._load_lock()
    assert store.get_lock_calls == 1

    clock[0] += 0.2  # now 5.1s past the cached read
    manager._load_lock()
    assert store.get_lock_calls == 2


def test_own_lock_write_refreshes_cached_doc(tmp_path: Path) -> None:
    store = _CountingSkillStore(memory_skill_store())
    manager = SkillManager(
        store=store,
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
    )

    manager._load_lock()
    assert store.get_lock_calls == 1

    written = SkillsLock(
        skills={_KEY: LockedSkill(repo="https://example.com/s.git", refs={"main": "abc123"})}
    )
    with manager._lock_write_lock:
        manager._write_lock_unlocked(written)

    loaded = manager._load_lock()
    assert store.get_lock_calls == 1  # served from the write-through cache
    assert loaded.skills[_KEY].refs == {"main": "abc123"}


def test_has_commit_memoizes_positive_results(tmp_path: Path) -> None:
    manager = _make_manager_with_cache(tmp_path)
    commit = _head_commit(tmp_path / "skills" / "demo_workflow" / "generate_key_info")
    cache_dir = tmp_path / "skills" / "demo_workflow" / "generate_key_info"

    calls: list[list[str]] = []
    real_run_git = manager._run_git

    def spy(args: list[str], check: bool = True):  # noqa: ANN202
        calls.append(args)
        return real_run_git(args, check=check)

    manager._run_git = spy  # type: ignore[method-assign]
    assert manager._has_commit(cache_dir, commit) is True
    assert manager._has_commit(cache_dir, commit) is True
    assert len(calls) == 1  # second call served from the in-process memo

    # Negative results are never memoized: a later commit may add the object.
    missing = "0" * 40
    calls.clear()
    assert manager._has_commit(cache_dir, missing) is False
    assert manager._has_commit(cache_dir, missing) is False
    assert len(calls) == 2


def _probe_counts(manager: SkillManager) -> dict[str, int]:
    """Count rev-parse/status probes issued through manager._run_git."""
    counts = {"status": 0, "rev-parse": 0}
    real_run_git = manager._run_git

    def spy(args: list[str], check: bool = True):  # noqa: ANN202
        for probe in counts:
            if probe in args:
                counts[probe] += 1
        return real_run_git(args, check=check)

    manager._run_git = spy  # type: ignore[method-assign]
    return counts


def test_cleanliness_probes_run_on_every_checkout(tmp_path: Path) -> None:
    """无 memo（PR 317 codex P1，退役 #42 的 TTL 快速通道）：每次 dispatch 都在
    cache 锁内重跑 rev-parse/status 探测——两个 SkillManager 实例交替 checkout
    同一 skill 的不同 ref 时，实例私有 memo 会让后者把错版本的 cache 复制进
    run 目录。正确性优先，每次两次 git 探测是可接受成本。"""
    _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)
    manager.get_skill_dir(_KEY, str(uuid.uuid4()))  # first probes
    counts = _probe_counts(manager)

    manager.get_skill_dir(_KEY, str(uuid.uuid4()))

    assert counts["status"] > 0
    assert counts["rev-parse"] > 0


def test_dirty_cache_repaired_on_next_checkout(tmp_path: Path) -> None:
    """脏 cache（stray 文件）在下一次 checkout 即被 checkout+clean 修复。"""
    _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)
    manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    cache_dir = tmp_path / "skills" / "demo_workflow" / "generate_key_info"
    stray = cache_dir / "stray.txt"
    stray.write_text("junk")

    served = manager.get_skill_dir(_KEY, str(uuid.uuid4()))

    assert not stray.exists()
    assert not (served / "stray.txt").exists()


def test_checkout_reprobes_cache_after_another_instance_switch(tmp_path: Path) -> None:
    """回归（PR 317 codex P1）：manager A checkout v1 后，实例 B 把共享 cache
    切到 v2；A 再 checkout v1 必须重新探测并拿到 v1 的内容——已退役的实例
    私有 memo 会在 TTL 内跳过探测，把 v2 内容复制进 run 目录却记录 v1@commit。"""
    repo = _make_skill_repo(tmp_path / "skills")
    _tag(repo, "v1")
    v1_content = (repo / "SKILL.md").read_text()
    _commit_skill_update(repo, "# skill v2\n")
    _tag(repo, "v2")

    store = memory_skill_store()
    manager_a = SkillManager(store=store, base_dir=tmp_path / "skills", runs_dir=tmp_path / "runs")
    manager_b = SkillManager(store=store, base_dir=tmp_path / "skills", runs_dir=tmp_path / "runs")

    manager_a.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1")
    # 另一个实例（共享 base_dir/runs_dir 与 FileLock 域）把 cache 切到 v2。
    manager_b.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v2")

    served = manager_a.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1")

    assert (served / "SKILL.md").read_text() == v1_content
