"""Derived-name lock shared by init/clean/drop worktree scripts (#1084).

The #950 collision check was a one-shot pre-check: ``drop-worktree-db.sh``
could sit at its confirmation prompt while another process created and
initialized a worktree with the same derived name, then drop that new
worktree's database. The three scripts now hold one machine-wide lock
(``<git common dir>/worktree-derived-names.lock``) from the collision
re-check through the create/delete, and take it only *after* any
interactive confirmation.

Timing discipline: every wait below is on a signal (an event-log line, the
waiter's "lock busy" announcement, a process exit), never a fixed sleep;
the deadlines are generous budgets for loaded CI machines.
"""

from __future__ import annotations

import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "scripts" / "worktree-names-lib.sh"
_SCRIPTS = ("drop-worktree-db.sh", "worktree-names-lib.sh")
_GIT = shutil.which("git")
_BUSY = "派生名锁被占用"
_DEADLINE = 30.0

# 持锁进程：取锁 -> 记 acquired -> 等 RELEASE 文件出现 -> 记 released -> 退出
# （EXIT trap 释放锁）。NESTED_CMD 非空时在持锁期间执行它（子进程继承导出
# 的锁标识），再记录父进程的 holder 文件是否仍在。
_HOLDER = """#!/usr/bin/env bash
set -euo pipefail
source "$LIB"
worktree_name_lock_acquire "$REPO" "holder $TAG"
echo "acquired $TAG" >>"$EVENTS"
if [[ -n "${NESTED_CMD:-}" ]]; then
    bash -c "$NESTED_CMD"
    if [[ -e "$AGENT_LEGION_WT_NAME_LOCK_FILE" ]]; then
        echo "parent-still-holds $TAG" >>"$EVENTS"
    fi
fi
while [[ ! -e "$RELEASE" ]]; do sleep 0.05; done
echo "released $TAG" >>"$EVENTS"
"""

# psql 桩：STUB_DBS（空格分隔）里的库视为存在；dev role 查询查不到。
_PSQL_STUB = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >>"$STUB_LOG"
query="${*: -1}"
for db in $STUB_DBS; do
    if [[ "$query" == *"datname='$db'"* ]]; then
        if [[ "$query" == *pg_size_pretty* ]]; then echo "Admin|8 kB"; else echo 1; fi
    fi
done
exit 0
"""

_DROPDB_STUB = """#!/usr/bin/env bash
printf 'dropdb %s\\n' "$*" >>"$STUB_LOG"
"""


def _exe(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _wait_for(cond: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + _DEADLINE
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for: {what}")
        time.sleep(0.02)


def _default_sigint() -> None:
    # A parent that ignores SIGINT (some runners do) would make it untrappable
    # in the child bash; restore the default so the INT trap is exercised.
    signal.signal(signal.SIGINT, signal.SIG_DFL)


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        assert _GIT is not None
        self.tmp = tmp_path.resolve()
        (self.tmp / "home").mkdir()
        self.main = self.tmp / "main"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.events = self.tmp / "events.log"
        self.events.write_text("")
        self.log = self.tmp / "stub.log"
        self.log.write_text("")
        self.holder = self.tmp / "holder.sh"
        _exe(self.holder, _HOLDER)
        _exe(self.bin / "git", f'#!/usr/bin/env bash\nexec "{_GIT}" "$@"\n')
        _exe(self.bin / "psql", _PSQL_STUB)
        _exe(self.bin / "dropdb", _DROPDB_STUB)
        self._git("init", "-q", str(self.main))
        self._git("-C", str(self.main), "commit", "-q", "--allow-empty", "-m", "init")
        self.lock_dir = self.main / ".git" / "worktree-derived-names.lock"
        self._n = 0

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "HOME": str(self.tmp / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STUB_LOG": str(self.log),
            "STUB_DBS": "",
            "LIB": str(LIB),
            "REPO": str(self.main),
            "EVENTS": str(self.events),
            **extra,
        }

    def _git(self, *args: str) -> None:
        assert _GIT is not None
        subprocess.run([_GIT, *args], check=True, capture_output=True, env=self.env())

    def add(self, name: str) -> Path:
        self._n += 1
        path = self.main / ".worktrees" / name
        self._git("-C", str(self.main), "worktree", "add", "-q", "-b", f"b{self._n}", str(path))
        return path

    def runner(self) -> Path:
        runner = self.add("runner")
        (runner / "scripts").mkdir()
        for name in _SCRIPTS:
            shutil.copy(ROOT / "scripts" / name, runner / "scripts" / name)
        return runner / "scripts"

    def events_lines(self) -> list[str]:
        return self.events.read_text().splitlines()

    def holders(self) -> list[str]:
        if not self.lock_dir.exists():
            return []
        return sorted(p.name for p in self.lock_dir.iterdir())

    def start_holder(self, tag: str, **extra: str) -> tuple[subprocess.Popen[str], Path]:
        release = self.tmp / f"release-{tag}"
        stderr = self.tmp / f"holder-{tag}.err"
        proc = subprocess.Popen(
            ["bash", str(self.holder)],
            env=self.env(TAG=tag, RELEASE=str(release), **extra),
            stdout=subprocess.DEVNULL,
            stderr=stderr.open("w"),
            text=True,
            preexec_fn=_default_sigint,
        )
        return proc, release


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    if _GIT is None:
        pytest.skip("git not available")
    return _Env(tmp_path)


# --- lock primitive -----------------------------------------------------------


def test_second_acquirer_waits_until_holder_releases(env: _Env) -> None:
    first, release_a = env.start_holder("a")
    _wait_for(lambda: "acquired a" in env.events_lines(), "a acquires")

    second, release_b = env.start_holder("b")
    release_b.touch()  # b exits right after it gets the lock
    err_b = env.tmp / "holder-b.err"
    _wait_for(lambda: _BUSY in err_b.read_text(), "b announces it is waiting")
    assert "acquired b" not in env.events_lines()
    assert "holder a" in err_b.read_text()  # the announcement names the holder

    release_a.touch()
    assert first.wait(timeout=_DEADLINE) == 0
    assert second.wait(timeout=_DEADLINE) == 0
    assert env.events_lines() == ["acquired a", "released a", "acquired b", "released b"]
    assert env.holders() == []


@pytest.mark.parametrize(
    ("sig", "trap_releases"),
    [
        (signal.SIGTERM, True),
        (signal.SIGINT, True),
        (signal.SIGHUP, True),
        # SIGKILL cannot be trapped: the holder file stays, and the next
        # contender reclaims it because its pid is dead.
        (signal.SIGKILL, False),
    ],
)
def test_lock_is_freed_when_holder_is_killed(
    env: _Env, sig: signal.Signals, trap_releases: bool
) -> None:
    first, _ = env.start_holder("a")
    _wait_for(lambda: "acquired a" in env.events_lines(), "a acquires")

    first.send_signal(sig)
    first.wait(timeout=_DEADLINE)
    assert "released a" not in env.events_lines()
    assert (env.holders() == []) is trap_releases

    second, release_b = env.start_holder("b")
    release_b.touch()
    assert second.wait(timeout=_DEADLINE) == 0
    assert env.events_lines()[-2:] == ["acquired b", "released b"]
    assert env.holders() == []


def test_acquire_refuses_without_git_common_dir(env: _Env) -> None:
    not_a_repo = env.tmp / "plain"
    not_a_repo.mkdir()
    result = subprocess.run(
        ["bash", "-c", 'source "$LIB"; worktree_name_lock_acquire "$REPO" "测试动作"'],
        env=env.env(REPO=str(not_a_repo), GIT_CEILING_DIRECTORIES=str(env.tmp)),
        capture_output=True,
        text=True,
        timeout=_DEADLINE,
    )
    assert result.returncode == 1
    assert "拒绝测试动作" in result.stderr


# --- drop-worktree-db.sh -------------------------------------------------------


def test_nested_drop_reuses_parent_lock_without_deadlock(env: _Env) -> None:
    """clean-worktree.sh 形态：已持锁的父进程转调 drop，drop 不再等锁、也不放掉父锁。"""
    scripts = env.runner()
    nested = f'"{scripts}/drop-worktree-db.sh" foo_bar --yes'
    first, release_a = env.start_holder(
        "a", NESTED_CMD=nested, STUB_DBS="agent_legion_foo_bar agent_legion_test_foo_bar"
    )
    _wait_for(lambda: "parent-still-holds a" in env.events_lines(), "nested drop finishes")
    release_a.touch()
    assert first.wait(timeout=_DEADLINE) == 0

    assert _BUSY not in (env.tmp / "holder-a.err").read_text()
    log = env.log.read_text()
    assert "dropdb agent_legion_foo_bar\n" in log
    assert "dropdb agent_legion_test_foo_bar\n" in log
    assert env.holders() == []


@pytest.mark.parametrize("conflict_appears", [True, False])
def test_drop_confirms_unlocked_then_rechecks_under_lock(env: _Env, conflict_appears: bool) -> None:
    """#1084 时序：drop 确认后等锁期间，派生名相同的新 worktree 完成注册。"""
    scripts = env.runner()
    first, release_a = env.start_holder("a")
    _wait_for(lambda: "acquired a" in env.events_lines(), "a acquires")

    drop_err = env.tmp / "drop.err"
    answer = env.tmp / "answer.txt"
    answer.write_text("foo_bar\n")
    drop = subprocess.Popen(
        ["bash", str(scripts / "drop-worktree-db.sh"), "foo_bar"],
        env=env.env(STUB_DBS="agent_legion_foo_bar agent_legion_test_foo_bar"),
        stdin=answer.open(),
        stdout=subprocess.PIPE,
        stderr=drop_err.open("w"),
        text=True,
        cwd=env.main,
    )
    # 确认已读完（stdin 应答在前）才开始等锁：确认阶段不持锁（取消路径见
    # test_drop_cancel_never_takes_the_lock）。
    _wait_for(lambda: _BUSY in drop_err.read_text(), "drop waits for the lock")
    assert "dropdb" not in env.log.read_text()

    if conflict_appears:
        env.add("foo-bar")  # 同派生名的新 worktree 在确认后、取锁前注册
    release_a.touch()
    assert first.wait(timeout=_DEADLINE) == 0
    stdout, _ = drop.communicate(timeout=_DEADLINE)

    log = env.log.read_text()
    if conflict_appears:
        assert drop.returncode == 1, stdout
        assert "拒绝删除派生库" in drop_err.read_text()
        assert "foo-bar -> agent_legion_foo_bar" in drop_err.read_text()
        assert "dropdb" not in log
    else:
        assert drop.returncode == 0, drop_err.read_text()
        assert "dropdb agent_legion_foo_bar\n" in log
        assert "dropdb agent_legion_test_foo_bar\n" in log
    assert env.holders() == []


def test_drop_cancel_never_takes_the_lock(env: _Env) -> None:
    scripts = env.runner()
    first, release_a = env.start_holder("a")
    _wait_for(lambda: "acquired a" in env.events_lines(), "a acquires")

    result = subprocess.run(
        ["bash", str(scripts / "drop-worktree-db.sh"), "foo_bar"],
        env=env.env(STUB_DBS="agent_legion_foo_bar"),
        input="nope\n",
        capture_output=True,
        text=True,
        cwd=env.main,
        timeout=_DEADLINE,
    )
    release_a.touch()
    assert first.wait(timeout=_DEADLINE) == 0

    assert result.returncode == 1
    assert "已取消" in result.stderr
    assert _BUSY not in result.stderr
    assert "dropdb" not in env.log.read_text()


def test_init_holds_lock_across_collision_check(env: _Env) -> None:
    """init 被持锁者挡住时停在撞名检查之前：放锁后才检查并建库。"""
    target = env.add("foo-baz")
    (target / "scripts").mkdir()
    for name in ("init-worktree.sh", "worktree-names-lib.sh", "ensure-s3-bucket.py"):
        shutil.copy(ROOT / "scripts" / name, target / "scripts" / name)
    (target / ".env").write_text("# stub env\n")
    _exe(env.bin / "uv", '#!/usr/bin/env bash\necho "stub-vault-master-key"\n')
    first, release_a = env.start_holder("a")
    _wait_for(lambda: "acquired a" in env.events_lines(), "a acquires")

    init_err = env.tmp / "init.err"
    init = subprocess.Popen(
        ["bash", str(target / "scripts" / "init-worktree.sh")],
        env=env.env(),
        stdout=subprocess.PIPE,
        stderr=init_err.open("w"),
        text=True,
    )
    _wait_for(lambda: _BUSY in init_err.read_text(), "init waits for the lock")
    assert (target / ".env").read_text() == "# stub env\n"

    env.add("foo_baz")  # 等锁期间注册了撞名 worktree：放锁后的检查必须看见它
    release_a.touch()
    assert first.wait(timeout=_DEADLINE) == 0
    init.communicate(timeout=_DEADLINE)

    assert init.returncode == 1
    assert "派生出同名资源" in init_err.read_text()
    assert (target / ".env").read_text() == "# stub env\n"
    assert env.holders() == []


def test_pid_alive_helper_rejects_garbage(env: _Env) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$LIB"; for p in "" abc "1 2" "$$"; do'
            ' _wt_name_lock_pid_alive "$p" && echo "alive:$p" || echo "dead:$p"; done',
        ],
        env=env.env(),
        capture_output=True,
        text=True,
        timeout=_DEADLINE,
    )
    lines = result.stdout.splitlines()
    assert lines[:3] == ["dead:", "dead:abc", "dead:1 2"]
    assert lines[3].startswith("alive:") and lines[3] != "alive:"
