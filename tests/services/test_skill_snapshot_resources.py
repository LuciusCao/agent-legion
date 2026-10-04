"""Adversarial resource and framing tests, independent of file contents."""

import subprocess
import sys
import time

import pytest

from server.app.services import skill_snapshot_git as transport
from server.app.services.skill_commit_snapshot import commit_snapshot
from server.app.services.skill_repo_edit import SkillEditValidationError
from tests.helpers.skill_snapshot import commit, git

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("count", [1, 101, 1001])
def test_snapshot_uses_two_git_processes_regardless_of_file_count(tmp_path, monkeypatch, count):
    git(tmp_path, "init", "-q")
    for index in range(count):
        (tmp_path / str(index)).write_bytes(b"\x00\n" if index % 2 else b"")
    commit(tmp_path)
    head = git(tmp_path, "rev-parse", "HEAD").decode().strip()
    original = subprocess.Popen
    calls = []

    def record(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", record)
    assert len(commit_snapshot(tmp_path, head)) == count
    assert [cmd[3] for cmd in calls] == ["ls-tree", "cat-file"]


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"abc missing\n",
        b"abc tree 2\nhi\n",
        b"abc blob 3\nhi\n",
        b"abc blob 2\nh",
        b"abc blob 2\nhi\nx",
    ],
)
def test_batch_rejects_protocol_disagreement(tmp_path, monkeypatch, raw):
    reader = transport.SnapshotGit(tmp_path)
    monkeypatch.setattr(reader, "run", lambda *args: raw)
    with pytest.raises(ValueError):
        transport.batch_blobs(reader, [("file", "abc", 2)])


def child_command(monkeypatch, script):
    original = subprocess.Popen
    children = []

    def launch(args, **kwargs):
        process = original([sys.executable, "-c", script], **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", launch)
    return children


@pytest.mark.parametrize("failure", ["overflow", "timeout", "exit"])
def test_transport_reaps_child_on_failure(tmp_path, monkeypatch, failure):
    scripts = {
        "overflow": "import os,time; os.write(1,b'x'*4096); time.sleep(60)",
        "timeout": "import time; time.sleep(60)",
        "exit": "raise SystemExit(2)",
    }
    children = child_command(monkeypatch, scripts[failure])
    reader = transport.SnapshotGit(tmp_path)
    reader.deadline = time.monotonic() + 0.2
    with pytest.raises(SkillEditValidationError):
        reader.run(["unused"], 128)
    assert len(children) == 1 and children[0].poll() is not None


def test_large_bidirectional_pipes_do_not_deadlock(tmp_path, monkeypatch):
    child_command(
        monkeypatch,
        "import os; os.write(1,b'x'*200000); "
        "data=b''\nwhile chunk:=os.read(0,65536): data+=chunk\n"
        "os.write(1,str(len(data)).encode())",
    )
    reader = transport.SnapshotGit(tmp_path)
    result = reader.run(["unused"], 200006, b"y" * 200000)
    assert result == b"x" * 200000 + b"200000"


def test_deadline_is_shared_between_commands(tmp_path, monkeypatch):
    children = child_command(monkeypatch, "print('ok')")
    reader = transport.SnapshotGit(tmp_path)
    assert reader.run(["first"], 3) == b"ok\n"
    reader.deadline = time.monotonic() - 1
    with pytest.raises(SkillEditValidationError):
        reader.run(["second"], 3)
    assert len(children) == 1


def test_empty_directories_consume_shared_traversal_budget(tmp_path, monkeypatch):
    from server.app.services import skill_edit_snapshot

    for index in range(4):
        (tmp_path / str(index)).mkdir()
    monkeypatch.setattr(skill_edit_snapshot, "MAX_TREE_ENTRIES", 3)
    with pytest.raises(SkillEditValidationError):
        skill_edit_snapshot.edit_tree(tmp_path)


@pytest.mark.parametrize("limit", [2, 3, 4])
def test_output_limit_is_inclusive(tmp_path, monkeypatch, limit):
    children = child_command(monkeypatch, "import os; os.write(1,b'abc')")
    reader = transport.SnapshotGit(tmp_path)
    if limit < 3:
        with pytest.raises(SkillEditValidationError):
            reader.run(["unused"], limit)
    else:
        assert reader.run(["unused"], limit) == b"abc"
    assert children[0].poll() is not None


def test_contended_edit_lock_fails_before_starting_git(tmp_path, monkeypatch):
    from filelock import FileLock

    from server.app.services import skill_edit_detail
    from server.app.services.job_errors import ConflictError

    lock_path = tmp_path / "edit.lock"
    monkeypatch.setattr(transport, "SNAPSHOT_SECONDS", 0.05)
    monkeypatch.setattr(skill_edit_detail, "edit_lock_for", lambda *args: FileLock(lock_path))
    children = child_command(monkeypatch, "raise AssertionError('must not run Git')")
    with FileLock(lock_path), pytest.raises(ConflictError):
        skill_edit_detail.editing_detail("w/s", tmp_path, None, tmp_path, None)
    assert children == []


def test_deep_empty_shared_tree_is_bounded_before_recursion_limit(tmp_path):
    from server.app.services.skill_edit_snapshot import edit_tree

    path = tmp_path
    for _ in range(60):
        path /= "directory"
        path.mkdir()
    with pytest.raises(SkillEditValidationError):
        edit_tree(tmp_path)
