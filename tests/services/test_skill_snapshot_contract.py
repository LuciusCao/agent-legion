"""Git authority, HEAD/tag parity and bounded, lossless authoring snapshots."""

import pytest

from server.app.services.skill_catalog import SkillCatalogService
from server.app.services.skill_repo_edit import SkillEditValidationError
from tests.helpers.skill_snapshot import commit, git

pytestmark = pytest.mark.no_db


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "skills/ws/example"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "SKILL.md").write_bytes(b"committed\r\n")
    (repo / ".gitignore").write_text(".env\nignored/\n")
    commit(repo)
    git(repo, "tag", "v1")
    return repo


def snapshot(repo, ref):
    return SkillCatalogService(None, base_dir=repo.parents[1]).detail(
        "ws/example", ref=ref, for_edit=True, runs_dir=repo.parents[2] / "runs"
    )


@pytest.mark.parametrize("ref", [None, "v1"])
@pytest.mark.parametrize(
    "local_state", ["ignored", "untracked", "modified", "staged", "deleted", "symlink"]
)
def test_local_state_never_changes_committed_edit_snapshot(repository, ref, local_state):
    repo = repository
    if local_state == "ignored":
        (repo / ".env").write_text("host-secret")
    elif local_state == "untracked":
        (repo / "private.txt").write_text("host-secret")
    elif local_state in ("modified", "staged"):
        (repo / "SKILL.md").write_text("host-secret")
        if local_state == "staged":
            git(repo, "add", "SKILL.md")
    else:
        (repo / "SKILL.md").unlink()
        if local_state == "symlink":
            outside = repo.parent / "private"
            outside.write_text("host-secret")
            (repo / "SKILL.md").symlink_to(outside)
    result = snapshot(repo, ref)
    assert {f["path"]: f["content"] for f in result["files"]} == {
        "SKILL.md": "committed\r\n",
        ".gitignore": ".env\nignored/\n",
    }
    assert result["commit"] == git(repo, "rev-parse", "HEAD").decode().strip()


@pytest.mark.parametrize("ref", [None, "v2"])
@pytest.mark.parametrize("count", [99, 100, 101])
def test_repository_size_is_not_a_save_batch_limit(repository, ref, count):
    repo = repository
    for i in range(count - 2):
        (repo / f"file-{i}").write_bytes("中文\\\x00\r\n".encode())
    commit(repo)
    git(repo, "tag", "v2")
    result = snapshot(repo, ref)
    assert len(result["files"]) == count
    assert all(not f["truncated"] for f in result["files"])


@pytest.mark.parametrize("ref", [None, "v2"])
def test_tag_and_head_read_distinct_commits(repository, ref):
    repo = repository
    (repo / "SKILL.md").write_text("second")
    commit(repo)
    git(repo, "tag", "v2")
    (repo / "SKILL.md").write_text("third")
    commit(repo)
    files = {f["path"]: f["content"] for f in snapshot(repo, ref)["files"]}
    assert files["SKILL.md"] == ("third" if ref is None else "second")


@pytest.mark.parametrize("ref", [None, "v2"])
@pytest.mark.parametrize("kind", ["invalid-utf8", "oversized", "symlink", "gitlink"])
def test_unsupported_committed_members_reject_entire_snapshot(repository, ref, kind):
    repo = repository
    if kind == "gitlink":
        head = git(repo, "rev-parse", "HEAD").decode().strip()
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},submodule")
        git(repo, "commit", "-qm", "gitlink", "--no-gpg-sign")
    else:
        path = repo / "unsupported"
        if kind == "symlink":
            path.symlink_to("SKILL.md")
        else:
            path.write_bytes(b"\xff" if kind == "invalid-utf8" else b"x" * (128 * 1024 + 1))
        commit(repo)
    git(repo, "tag", "v2")
    with pytest.raises(SkillEditValidationError):
        snapshot(repo, ref)


@pytest.mark.parametrize("ref", [None, "v2"])
@pytest.mark.parametrize("size", [128 * 1024 - 1, 128 * 1024])
def test_file_byte_boundary_and_unusual_paths_round_trip(repository, ref, size):
    repo = repository
    path = repo / "中文\tline\nfile"
    path.write_bytes(b"\x00" * size)
    path.chmod(0o755)
    commit(repo)
    git(repo, "tag", "v2")
    result = snapshot(repo, ref)
    item = next(f for f in result["files"] if f["path"] == path.name)
    assert item["content"].encode() == path.read_bytes()
    assert item["size"] == size


@pytest.mark.parametrize("ref", [None, "v2"])
def test_total_budget_rejects_before_reading_any_blob(repository, ref, monkeypatch):
    from server.app.services import skill_commit_snapshot, skill_repo

    repo = repository
    (repo / "a").write_bytes(b"x" * 2048)
    (repo / "b").write_bytes(b"x" * 2048)
    commit(repo)
    git(repo, "tag", "v2")
    monkeypatch.setattr(skill_commit_snapshot, "MAX_SNAPSHOT_BYTES", 4096)
    original = skill_repo.run_git

    def checked(repo, args, **kwargs):
        assert args[0] != "cat-file", "must preflight whole tree before loading blobs"
        return original(repo, args, **kwargs)

    monkeypatch.setattr(skill_repo, "run_git", checked)
    with pytest.raises(SkillEditValidationError):
        snapshot(repo, ref)


@pytest.mark.parametrize("ref", [None, "v1"])
def test_snapshot_stays_on_resolved_commit_if_head_moves(repository, ref, monkeypatch):
    from server.app.services import skill_repo

    repo = repository
    original = skill_repo.run_git
    moved = False

    def move_head(repo, args, **kwargs):
        nonlocal moved
        result = original(repo, args, **kwargs)
        if args[0] == "ls-tree" and not moved:
            moved = True
            (repo / "SKILL.md").write_text("new head")
            commit(repo)
        return result

    monkeypatch.setattr(skill_repo, "run_git", move_head)
    result = snapshot(repo, ref)
    assert next(f for f in result["files"] if f["path"] == "SKILL.md")["content"] == "committed\r\n"
    assert result["commit"] != git(repo, "rev-parse", "HEAD").decode().strip()


@pytest.mark.parametrize("ref", [None, "missing"])
@pytest.mark.parametrize("state", ["missing", "unborn", "nested-nonrepo"])
def test_unavailable_commit_never_falls_back_to_parent_repository(repository, ref, state):
    from server.app.services.job_errors import NotFoundError

    if state == "missing":
        repository = repository.parent / "missing"
    elif state == "unborn":
        repository = repository.parent / "unborn"
        repository.mkdir()
        git(repository, "init", "-q")
    else:
        repository = repository / "nested"
        repository.mkdir()
    with pytest.raises(NotFoundError):
        SkillCatalogService(None, base_dir=repository.parents[1]).detail(
            f"{repository.parent.name}/{repository.name}",
            ref=ref,
            for_edit=True,
            runs_dir=repository.parents[2] / "runs",
        )


@pytest.mark.parametrize("ref", [None, "v2"])
@pytest.mark.parametrize("allowance", [-1, 0, 1])
def test_snapshot_total_budget_boundary(repository, ref, allowance, monkeypatch):
    from server.app.services import skill_commit_snapshot

    repo = repository
    (repo / "blob").write_bytes(b"x" * 4096)
    commit(repo)
    git(repo, "tag", "v2")
    expected = sum(
        len(f["content"].encode())
        + len(f["path"].encode())
        + skill_commit_snapshot.ENTRY_OVERHEAD_BYTES
        for f in snapshot(repo, ref)["files"]
    )
    monkeypatch.setattr(skill_commit_snapshot, "MAX_SNAPSHOT_BYTES", expected + allowance)
    if allowance < 0:
        with pytest.raises(SkillEditValidationError):
            snapshot(repo, ref)
    else:
        assert len(snapshot(repo, ref)["files"]) == 3


@pytest.mark.parametrize("ref", [None, "v2"])
@pytest.mark.parametrize("length", [512, 513])
def test_git_snapshot_path_matches_save_path_limit(repository, ref, length):
    repo = repository
    relative = "a" * 170 + "/" + "b" * 170 + "/" + "c" * (length - 342)
    path = repo / relative
    path.parent.mkdir(parents=True)
    path.write_text("content")
    commit(repo)
    git(repo, "tag", "v2")
    if length > 512:
        with pytest.raises(SkillEditValidationError):
            snapshot(repo, ref)
    else:
        assert relative in {f["path"] for f in snapshot(repo, ref)["files"]}
