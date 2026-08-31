"""Multi-ref skill lock semantics (issue #76 phase 1, #322 ref model).

Split from ``tests/test_skill_manager.py`` (file budget): the v2 lock shape
({repo, refs: {ref: commit}}) behaviors — ref drift freezing each ref
independently, auto-lock merge, relock over existing entries, and the
checkout_skill version string. Git fixtures live in
``tests/helpers/skill_git.py``.
"""

from __future__ import annotations

import concurrent.futures
import uuid
from pathlib import Path

import pytest

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


def test_ref_drift_locks_each_ref_independently(tmp_path: Path) -> None:
    """Multi-ref lock (issue #76): a ref drift is no longer an error — the
    new ref resolves and freezes next to the old pin, and each dispatch gets
    the commit pinned for the ref it asked for."""
    repo = _make_skill_repo(tmp_path / "skills")
    v1_commit = _tag(repo, "v1.0.0")
    manager = SkillManager(
        store=memory_skill_store(),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
        doc_cache_ttl_seconds=0,
    )
    first_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1.0.0")
    assert (first_dir / "SKILL.md").read_text() == "# skill\n"

    main_commit = _commit_skill_update(repo, "# updated skill\n")
    main_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="main")
    assert (main_dir / "SKILL.md").read_text() == "# updated skill\n"

    # The auto-lock added the new ref without disturbing the existing pin.
    refs = manager.load_lock().skills[_KEY].refs
    assert set(refs) == {"v1.0.0", "main"}
    assert refs["v1.0.0"] == v1_commit
    assert refs["main"] == main_commit

    # An explicit older ref still resolves to its own frozen commit.
    run_dir, commit, version = manager.checkout_skill(_KEY, str(uuid.uuid4()), ref="v1.0.0")
    assert commit == v1_commit
    assert version == f"v1.0.0@{v1_commit[:12]}"
    assert (run_dir / "SKILL.md").read_text() == "# skill\n"


def test_checkout_skill_returns_commit_and_version(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills")
    _tag(repo, "v1.0.0")
    manager = _make_manager(tmp_path)

    execution_id = str(uuid.uuid4())
    run_dir, commit, version = manager.checkout_skill(_KEY, execution_id, ref="v1.0.0")

    assert run_dir == tmp_path / "runs" / execution_id / "demo_workflow" / "generate_key_info"
    assert run_dir.is_dir()
    assert commit == manager.load_lock().skills[_KEY].refs["v1.0.0"]
    assert version == f"v1.0.0@{commit[:12]}"
    assert commit == _head_commit(repo)


def test_refresh_lock_keeps_previously_locked_refs(tmp_path: Path) -> None:
    """Relock (#322) iterates the lock's own entries and re-resolves every
    pinned ref: a moved tag refreshes its pin, and refs nobody relocked are
    kept (the union is the lock itself now — there is no source ref)."""
    from server.app.skills.lock import refresh_lock

    repo = _make_skill_repo(tmp_path / "skills")
    v1_commit = _tag(repo, "v1.0.0")
    _tag(repo, "v2.0.0")
    store = memory_skill_store()
    base_dir = tmp_path / "skills"
    manager = SkillManager(
        store=store,
        base_dir=base_dir,
        runs_dir=tmp_path / "runs",
        doc_cache_ttl_seconds=0,
    )
    manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v1.0.0")
    manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref="v2.0.0")

    _commit_skill_update(repo, "# updated skill\n")
    _tag(repo, "v2.0.0", force=True)
    refresh_lock(store, base_dir)

    refs = manager.load_lock().skills[_KEY].refs
    assert refs["v1.0.0"] == v1_commit
    assert refs["v2.0.0"] != v1_commit
    assert refs["v2.0.0"] == _head_commit(repo)


def test_concurrent_refs_auto_lock_retains_both_pins(tmp_path: Path) -> None:
    """Two refs of one skill racing their first dispatch must both land in the
    lock: the auto-lock merge adds a ref without rewriting existing pins."""
    repo = _make_skill_repo(tmp_path / "skills")
    v1_commit = _tag(repo, "v1.0.0")
    manager = SkillManager(
        store=memory_skill_store(),
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
        doc_cache_ttl_seconds=0,
    )

    def fetch(ref: str) -> Path:
        return manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref=ref)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(fetch, ref) for ref in ("v1.0.0", "main")]
        dirs = [f.result() for f in concurrent.futures.as_completed(futures)]

    refs = manager.load_lock().skills[_KEY].refs
    assert set(refs) == {"v1.0.0", "main"}
    assert refs["v1.0.0"] == v1_commit
    assert refs["main"] == v1_commit
    for skill_dir in dirs:
        assert (skill_dir / "SKILL.md").is_file()
