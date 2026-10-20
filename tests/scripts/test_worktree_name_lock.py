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
_SCRIPTS = ("clean-worktree.sh", "drop-worktree-db.sh", "worktree-names-lib.sh")
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

# dropdb 桩：记录调用与「此刻锁是否被持有」；RECREATE_WT 非空时模拟并发方
# 在删库之后、删 bucket 之前用同名重建 worktree（只做 git worktree add——
# 其 init 会被锁挡住）。BLOCK_RELEASE 非空时，victim 开发库的删除阻塞到该
# 文件出现（模拟父进程被 SIGKILL 时嵌套子进程仍在删库，#1084 R4）。
_DROPDB_STUB = """#!/usr/bin/env bash
if [[ -n "${BLOCK_RELEASE:-}" && "$*" == *agent_legion_victim && "$*" != *_test_* ]]; then
    echo "dropdb-blocked agent_legion_victim" >>"$STUB_LOG"
    while [[ ! -e "$BLOCK_RELEASE" ]]; do sleep 0.05; done
fi
printf 'dropdb %s\\n' "$*" >>"$STUB_LOG"
if ls "$LOCK_DIR"/holder-* >/dev/null 2>&1; then echo "dropdb-under-lock" >>"$STUB_LOG"; fi
if [[ -n "${RECREATE_WT:-}" && ! -d "$REPO/.worktrees/$RECREATE_WT" ]]; then
    git -C "$REPO" worktree add -q -b "recreated-$RECREATE_WT" "$REPO/.worktrees/$RECREATE_WT"
fi
"""

# uv 桩：clean 的 bucket 两阶段（probe 报待删、delete 记录是否持锁）；其余
# 调用（init 的 vault key）输出一行。BLOCK_RELEASE 非空时 delete 阻塞到该
# 文件出现（模拟父进程被 SIGKILL 时 delete 子进程仍在删，#1084 R4）。
_UV_STUB = """#!/usr/bin/env bash
case "${CLEAN_WORKTREE_PHASE:-}" in
    probe) echo "待删除 bucket: stub"; exit 10 ;;
    delete)
        echo "bucket-delete-start" >>"$STUB_LOG"
        if [[ -n "${BLOCK_RELEASE:-}" ]]; then
            while [[ ! -e "$BLOCK_RELEASE" ]]; do sleep 0.05; done
        fi
        echo "bucket-delete-done" >>"$STUB_LOG"
        if ls "$LOCK_DIR"/holder-* >/dev/null 2>&1; then echo "bucket-delete-under-lock" >>"$STUB_LOG"; fi
        echo "stub delete_bucket"; exit 0 ;;
esac
echo "stub-vault-master-key"
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
        _exe(self.bin / "uv", _UV_STUB)
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
            "LOCK_DIR": str(self.main / ".git" / "worktree-derived-names.lock"),
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


# None = 无人插队；"foo-bar" = 派生名相同的新 worktree；"foo_bar" = 同名重建。
@pytest.mark.parametrize("appears", ["foo-bar", "foo_bar", None])
def test_drop_confirms_unlocked_then_rechecks_under_lock(env: _Env, appears: str | None) -> None:
    """#1084 时序：drop 确认后等锁期间，派生名相同（含同名重建）的 worktree 注册。"""
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

    if appears is not None:
        env.add(appears)  # 确认后、取锁前注册
    release_a.touch()
    assert first.wait(timeout=_DEADLINE) == 0
    stdout, _ = drop.communicate(timeout=_DEADLINE)

    log = env.log.read_text()
    if appears is not None:
        assert drop.returncode == 1, stdout
        assert "拒绝删除派生库" in drop_err.read_text()
        assert f"{appears} -> agent_legion_foo_bar" in drop_err.read_text()
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


# --- clean-worktree.sh ---------------------------------------------------------

_VICTIM_DBS = "agent_legion_victim agent_legion_test_victim"


def test_clean_takes_lock_before_removing_worktree(env: _Env) -> None:
    """锁在第 1 步之前取得、持有到删库与删 bucket 结束（codex #1246 R1 P1）。"""
    scripts = env.runner()
    victim = env.add("victim")
    first, release_a = env.start_holder("a")
    _wait_for(lambda: "acquired a" in env.events_lines(), "a acquires")

    clean_err = env.tmp / "clean.err"
    clean = subprocess.Popen(
        ["bash", str(scripts / "clean-worktree.sh"), "victim", "--yes"],
        env=env.env(STUB_DBS=_VICTIM_DBS),
        stdout=subprocess.PIPE,
        stderr=clean_err.open("w"),
        text=True,
        cwd=env.main,
    )
    _wait_for(lambda: _BUSY in clean_err.read_text(), "clean waits for the lock")
    assert victim.is_dir()  # 等锁时还没动 worktree
    assert "dropdb" not in env.log.read_text()

    release_a.touch()
    assert first.wait(timeout=_DEADLINE) == 0
    stdout, _ = clean.communicate(timeout=_DEADLINE)

    assert clean.returncode == 0, clean_err.read_text()
    assert not victim.exists()
    log = env.log.read_text()
    assert "dropdb agent_legion_victim\n" in log
    assert "dropdb-under-lock" in log
    assert "bucket-delete-under-lock" in log
    assert "stub delete_bucket" in stdout
    assert env.holders() == []


def test_clean_refuses_bucket_when_same_name_worktree_is_recreated(env: _Env) -> None:
    """删库后同名 worktree 被重建：删 bucket 前的重查把它算作冲突。"""
    scripts = env.runner()
    env.add("victim")

    result = subprocess.run(
        ["bash", str(scripts / "clean-worktree.sh"), "victim", "--yes"],
        env=env.env(STUB_DBS=_VICTIM_DBS, RECREATE_WT="victim"),
        capture_output=True,
        text=True,
        cwd=env.main,
        timeout=_DEADLINE,
    )

    assert result.returncode == 1, result.stdout
    assert "拒绝删除派生 bucket" in result.stderr
    assert "同名 worktree 本身" in result.stderr
    assert "stub delete_bucket" not in result.stdout
    assert "bucket-delete-under-lock" not in env.log.read_text()
    assert env.holders() == []


def test_clean_single_confirmation_happens_before_lock(env: _Env) -> None:
    """无 --yes：一次确认覆盖库与 bucket，取消时既不取锁也不动 worktree。"""
    scripts = env.runner()
    victim = env.add("victim")
    first, release_a = env.start_holder("a")
    _wait_for(lambda: "acquired a" in env.events_lines(), "a acquires")

    result = subprocess.run(
        ["bash", str(scripts / "clean-worktree.sh"), "victim"],
        env=env.env(STUB_DBS=_VICTIM_DBS),
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
    assert victim.is_dir()
    assert "dropdb" not in env.log.read_text()


def test_drop_refuses_while_same_name_worktree_is_registered(env: _Env) -> None:
    scripts = env.runner()
    env.add("foo_bar")

    result = subprocess.run(
        ["bash", str(scripts / "drop-worktree-db.sh"), "foo_bar", "--yes"],
        env=env.env(STUB_DBS="agent_legion_foo_bar"),
        capture_output=True,
        text=True,
        cwd=env.main,
        timeout=_DEADLINE,
    )

    assert result.returncode == 1
    assert "foo_bar -> agent_legion_foo_bar" in result.stderr
    assert "同名 worktree 本身" in result.stderr
    assert "dropdb" not in env.log.read_text()


# --- 锁生命周期 vs 进程树（#1084 codex R4 P1）--------------------------------
#
# holder 只编码取锁进程 pid：父进程（clean）被 SIGKILL 后，借用锁的嵌套
# 子进程（drop）与后台执行的 bucket delete 仍在删资源，pid 判死回收会让
# 新竞争者与孤儿执行者并发。锁必须按「任一登记的共同执行者存活」判活。


def _start_clean_with_blocked_delete(
    env: _Env, block_release: Path, stub_dbs: str
) -> tuple[subprocess.Popen[str], Path]:
    scripts = env.runner()
    env.add("victim")
    clean_err = env.tmp / "clean-killed.err"
    clean = subprocess.Popen(
        ["bash", str(scripts / "clean-worktree.sh"), "victim", "--yes"],
        env=env.env(STUB_DBS=stub_dbs, BLOCK_RELEASE=str(block_release)),
        stdout=subprocess.PIPE,
        stderr=clean_err.open("w"),
        text=True,
        cwd=env.main,
    )
    return clean, clean_err


def _registered_children(env: _Env) -> list[str]:
    """锁目录里共同执行者登记文件（holder-*.child-*）。"""
    if not env.lock_dir.exists():
        return []
    return sorted(p.name for p in env.lock_dir.glob("holder-*.child-*"))


def test_lock_outlives_parent_killed_during_nested_drop(env: _Env) -> None:
    """clean 在嵌套 drop 删库期间被 SIGKILL：drop 完成前竞争者不得进临界区。"""
    block = env.tmp / "block-release"
    clean, _ = _start_clean_with_blocked_delete(env, block, _VICTIM_DBS)
    # 等嵌套 drop 进入第一个库的删除（桩阻塞点）
    _wait_for(
        lambda: "dropdb-blocked agent_legion_victim" in env.log.read_text(), "drop blocks in dropdb"
    )

    # 竞争者此时启动：必须等锁（看见持有者公告），不得直接拿到
    contender, release_t = env.start_holder("t")
    err_t = env.tmp / "holder-t.err"
    _wait_for(lambda: _BUSY in err_t.read_text(), "contender announces waiting")

    clean.kill()  # 只杀父 shell；drop 孤儿继续阻塞在删库中
    clean.wait(timeout=_DEADLINE)  # 回收 zombie：pid 判死才会失效

    # 父进程已死且已回收：锁必须因登记的共同执行者（drop）存活而保持——
    # 登记文件仍在、竞争者未拿到锁（确定性状态断言，不靠延时）。
    assert _registered_children(env) != []
    assert "acquired t" not in env.events_lines()

    block.touch()  # 放行 dropdb：drop 完成剩余删除后退出
    release_t.touch()
    _wait_for(lambda: "acquired t" in env.events_lines(), "contender eventually acquires")
    assert contender.wait(timeout=_DEADLINE) == 0

    # 竞争者拿到锁时，drop 的两个库都必须已经删完（锁按登记子进程判活）。
    log = env.log.read_text()
    assert "dropdb agent_legion_victim\n" in log
    assert "dropdb agent_legion_test_victim\n" in log
    _wait_for(lambda: env.holders() == [], "lock dir fully reclaimed")
    assert env.holders() == []


def test_lock_outlives_parent_killed_during_bucket_delete(env: _Env) -> None:
    """clean 在 bucket delete（多步删除，实际执行者是 uv/python 子进程）期间
    被 SIGKILL：delete 完成前竞争者不得进临界区。"""
    block = env.tmp / "block-release"
    # 无库可删（STUB_DBS 为空）：drop 快速跳过，阻塞点只在 bucket delete。
    clean, _ = _start_clean_with_blocked_delete(env, block, "")
    _wait_for(lambda: "bucket-delete-start" in env.log.read_text(), "bucket delete blocks")

    contender, release_t = env.start_holder("t")
    err_t = env.tmp / "holder-t.err"
    _wait_for(lambda: _BUSY in err_t.read_text(), "contender announces waiting")

    clean.kill()
    clean.wait(timeout=_DEADLINE)

    assert _registered_children(env) != []
    assert "acquired t" not in env.events_lines()

    block.touch()
    release_t.touch()
    _wait_for(lambda: "acquired t" in env.events_lines(), "contender eventually acquires")
    assert contender.wait(timeout=_DEADLINE) == 0

    # 竞争者拿到锁时 delete 必须已完成（顺序：delete 结束 → 锁才释放）。
    assert "bucket-delete-done" in env.log.read_text()
    _wait_for(lambda: env.holders() == [], "lock dir fully reclaimed")
    assert env.holders() == []
