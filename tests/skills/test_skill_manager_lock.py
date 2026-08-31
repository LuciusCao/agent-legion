"""Multi-ref skill lock semantics (issue #76 phase 1).

Split from ``tests/test_skill_manager.py`` (file budget): the v2 lock shape
({repo, refs: {ref: commit}}) behaviors — ref drift freezing each ref
independently, auto-lock merge, relock union, and the checkout_skill version
string. Git fixtures live in ``tests/helpers/skill_git.py``.
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import uuid
from pathlib import Path

import pytest

from server.app.skills.config import SkillsConfig
from server.app.skills.manager import SkillManager
from tests.helpers.skill_git import (
    _KEY,
    _git_env,
    _make_bare_repo,
    _make_manager,
    _push_new_commit,
    _single_skill,
)
from tests.helpers.skill_store import memory_skill_store

pytestmark = pytest.mark.no_db


def _tag_and_push(repo_uri: str, tmp_path: Path, tag: str) -> str:
    env = _git_env()
    work = tmp_path / "work" / "clone"
    head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(work), "tag", tag], check=True, env=env)
    subprocess.run(["git", "-C", str(work), "push", "origin", tag], check=True, env=env)
    return head


def test_ref_drift_locks_each_ref_independently(tmp_path: Path) -> None:
    """Multi-ref lock (issue #76): a source ref drift is no longer an error —
    the new ref resolves and freezes next to the old pin, and each dispatch
    gets the commit pinned for the ref it asked for."""
    repo_uri = _make_bare_repo(tmp_path)
    store = memory_skill_store(_single_skill(repo_uri, "v1.0.0"))
    manager = SkillManager(
        store=store,
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
        doc_cache_ttl_seconds=0,
    )
    v1_commit = _tag_and_push(repo_uri, tmp_path, "v1.0.0")
    first_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    assert (first_dir / "SKILL.md").read_text() == "# skill\n"

    _push_new_commit(repo_uri, tmp_path, "# updated skill\n")
    store.put_sources(SkillsConfig.model_validate({"skills": _single_skill(repo_uri, "main")}))
    main_dir = manager.get_skill_dir(_KEY, str(uuid.uuid4()))
    assert (main_dir / "SKILL.md").read_text() == "# updated skill\n"

    # The auto-lock added the new ref without disturbing the existing pin.
    refs = manager._load_lock().skills[_KEY].refs
    assert set(refs) == {"v1.0.0", "main"}
    assert refs["v1.0.0"] == v1_commit
    assert refs["main"] != v1_commit

    # An explicit older ref still resolves to its own frozen commit.
    run_dir, commit, version = manager.checkout_skill(_KEY, str(uuid.uuid4()), ref="v1.0.0")
    assert commit == v1_commit
    assert version == f"v1.0.0@{v1_commit[:12]}"
    assert (run_dir / "SKILL.md").read_text() == "# skill\n"


def test_checkout_skill_returns_commit_and_version(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    manager = _make_manager(tmp_path, _single_skill(repo_uri))

    execution_id = str(uuid.uuid4())
    run_dir, commit, version = manager.checkout_skill(_KEY, execution_id)

    assert run_dir == tmp_path / "runs" / execution_id / "demo_workflow" / "generate_key_info"
    assert run_dir.is_dir()
    assert commit == manager._load_lock().skills[_KEY].refs["main"]
    assert version == f"main@{commit[:12]}"


def test_refresh_lock_keeps_previously_locked_refs(tmp_path: Path) -> None:
    """Relock refreshes the source ref plus every ref already pinned for the
    skill (issue #76): switching the source ref must not drop the old pin."""
    from server.app.skills.lock import refresh_lock

    repo_uri = _make_bare_repo(tmp_path)
    store = memory_skill_store(_single_skill(repo_uri, "v1.0.0"))
    base_dir = tmp_path / "skills"
    manager = SkillManager(
        store=store,
        base_dir=base_dir,
        runs_dir=tmp_path / "runs",
        doc_cache_ttl_seconds=0,
    )
    v1_commit = _tag_and_push(repo_uri, tmp_path, "v1.0.0")
    manager.get_skill_dir(_KEY, str(uuid.uuid4()))

    _push_new_commit(repo_uri, tmp_path, "# updated skill\n")
    store.put_sources(SkillsConfig.model_validate({"skills": _single_skill(repo_uri, "main")}))
    refresh_lock(store, base_dir)

    refs = manager._load_lock().skills[_KEY].refs
    assert refs["v1.0.0"] == v1_commit
    assert refs["main"] != v1_commit


def test_concurrent_refs_auto_lock_retains_both_pins(tmp_path: Path) -> None:
    """Two refs of one skill racing their first dispatch must both land in the
    lock: the auto-lock merge adds a ref without rewriting existing pins."""
    repo_uri = _make_bare_repo(tmp_path)
    v1_commit = _tag_and_push(repo_uri, tmp_path, "v1.0.0")
    store = memory_skill_store(_single_skill(repo_uri))
    manager = SkillManager(
        store=store,
        base_dir=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
        doc_cache_ttl_seconds=0,
    )

    def fetch(ref: str) -> Path:
        return manager.get_skill_dir(_KEY, str(uuid.uuid4()), ref=ref)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(fetch, ref) for ref in ("v1.0.0", "main")]
        dirs = [f.result() for f in concurrent.futures.as_completed(futures)]

    refs = manager._load_lock().skills[_KEY].refs
    assert set(refs) == {"v1.0.0", "main"}
    assert refs["v1.0.0"] == v1_commit
    assert refs["main"] == v1_commit
    for skill_dir in dirs:
        assert (skill_dir / "SKILL.md").is_file()
