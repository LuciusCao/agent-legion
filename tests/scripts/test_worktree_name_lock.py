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
import sys
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
# （EXIT trap 释放锁）。
_HOLDER = """#!/usr/bin/env bash
set -euo pipefail
source "$LIB"
worktree_name_lock_acquire "$REPO" "holder $TAG"
echo "acquired $TAG" >>"$EVENTS"
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
# 其 init 会被锁挡住）。RECREATE_INITIALIZED=marker/legacy-env 时给重建实例
# 补上初始化完成标记/指向派生库的存量 .env（#1084 R5 资源主判定）。
# BLOCK_RELEASE 非空时，victim 开发库的删除阻塞到该文件出现（模拟持有者被
# SIGKILL 时在途命令尚未完成）。
_DROPDB_STUB = """#!/usr/bin/env bash
if [[ -n "${BLOCK_RELEASE:-}" && "$*" == *agent_legion_victim && "$*" != *_test_* ]]; then
    echo "dropdb-blocked agent_legion_victim" >>"$STUB_LOG"
    while [[ ! -e "$BLOCK_RELEASE" ]]; do sleep 0.05; done
fi
printf 'dropdb %s\\n' "$*" >>"$STUB_LOG"
if ls "$LOCK_DIR"/holder-* >/dev/null 2>&1; then echo "dropdb-under-lock" >>"$STUB_LOG"; fi
if [[ -n "${RECREATE_WT:-}" && ! -d "$REPO/.worktrees/$RECREATE_WT" ]]; then
    git -C "$REPO" worktree add -q -b "recreated-$RECREATE_WT" "$REPO/.worktrees/$RECREATE_WT"
    case "${RECREATE_INITIALIZED:-}" in
        marker) echo "initialized_at=stub" > "$REPO/.worktrees/$RECREATE_WT/.worktree-initialized.ok" ;;
        legacy-env)
            db="agent_legion_$(printf '%s' "$RECREATE_WT" | tr -c 'a-zA-Z0-9_' '_')"
            echo "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/$db" > "$REPO/.worktrees/$RECREATE_WT/.env"
            ;;
    esac
fi
"""

# uv 桩：clean 的 bucket 两阶段（probe 报待删、delete 记录是否持锁）；其余
# 调用（init 的 vault key）输出一行。
_UV_STUB = """#!/usr/bin/env bash
case "${CLEAN_WORKTREE_PHASE:-}" in
    probe) echo "待删除 bucket: stub"; exit 10 ;;
    delete)
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


# None = 无人插队。别名碰撞（foo-bar 与 foo_bar 归一派生同一库，#950）与
# 同名适用同一资源主判定（复验 P1）：有完成标记或存量 .env 指向派生库 =
# 资源主 → 拒绝；无标记无 .env = 阻塞在锁上的待初始化实例，不构成资源主 →
# 照删，其 init 放锁后在干净石板上自建。
@pytest.mark.parametrize(
    ("appears", "initialized", "refused"),
    [
        ("foo-bar", "marker", True),
        ("foo-bar", "legacy-env", True),
        ("foo-bar", None, False),
        ("foo_bar", "marker", True),
        ("foo_bar", "legacy-env", True),
        # export 前缀形式（init 的 replace_in_place 保留基准 .env 的 export
        # 形态）同样认定为资源主（codex P2：此前不认 export 行会误判为
        # 待初始化而绕过护栏）。
        ("foo_bar", "export-env", True),
        ("foo_bar", None, False),
        (None, None, False),
    ],
)
def test_drop_confirms_unlocked_then_rechecks_under_lock(
    env: _Env, appears: str | None, initialized: str | None, refused: bool
) -> None:
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
        added = env.add(appears)  # 确认后、取锁前注册
        if initialized == "marker":
            (added / ".worktree-initialized.ok").write_text("initialized_at=t\n", encoding="utf-8")
        elif initialized == "legacy-env":
            (added / ".env").write_text(
                "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_foo_bar\n",
                encoding="utf-8",
            )
        elif initialized == "export-env":
            (added / ".env").write_text(
                "export AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_foo_bar\n",
                encoding="utf-8",
            )
    release_a.touch()
    assert first.wait(timeout=_DEADLINE) == 0
    stdout, _ = drop.communicate(timeout=_DEADLINE)

    log = env.log.read_text()
    if refused:
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


@pytest.mark.parametrize(
    ("initialized", "refused"),
    [("marker", True), ("legacy-env", True), (None, False)],
)
def test_clean_bucket_delete_and_same_name_recreate(
    env: _Env, initialized: str | None, refused: bool
) -> None:
    """删库后同名 worktree 被重建：删 bucket 前的重查按初始化状态分流
    （#1084 R5）——已初始化（完成标记/存量 .env）= 资源主，拒绝；待初始化
    （无标记无 .env，init 阻塞在锁上）= 不构成资源主，照删，其 init 放锁后
    在干净石板上自建。"""
    scripts = env.runner()
    env.add("victim")

    result = subprocess.run(
        ["bash", str(scripts / "clean-worktree.sh"), "victim", "--yes"],
        env=env.env(
            STUB_DBS=_VICTIM_DBS, RECREATE_WT="victim", RECREATE_INITIALIZED=initialized or ""
        ),
        capture_output=True,
        text=True,
        cwd=env.main,
        timeout=_DEADLINE,
    )

    log = env.log.read_text()
    if refused:
        assert result.returncode == 1, result.stdout
        assert "拒绝删除派生 bucket" in result.stderr
        assert "同名 worktree 本身" in result.stderr
        assert "stub delete_bucket" not in result.stdout
        assert "bucket-delete-under-lock" not in log
    else:
        assert result.returncode == 0, result.stderr
        assert "stub delete_bucket" in result.stdout
        assert "bucket-delete-under-lock" in log
    # 两种形态下库都已删除（删库发生在重建之前）。
    assert "dropdb agent_legion_victim\n" in log
    assert env.holders() == []


@pytest.mark.parametrize(
    ("initialized", "refused"),
    [("marker", True), ("legacy-env", True), (None, False)],
)
def test_clean_bucket_delete_and_alias_recreate(
    env: _Env, initialized: str | None, refused: bool
) -> None:
    """别名重建（复验 P1）：clean 删 foo_bar 期间另一进程注册别名 foo-bar
    （归一派生同一库，#950）——owns-resources 判定覆盖别名登记：待初始化
    （无标记无 .env，init 阻塞在锁上）不构成资源主，照删不误拦（否则遗留
    资源会被它的 init 当作已有复用）；已初始化（标记/存量 .env）= 资源主，
    拒删。"""
    scripts = env.runner()
    env.add("foo_bar")

    result = subprocess.run(
        ["bash", str(scripts / "clean-worktree.sh"), "foo_bar", "--yes"],
        env=env.env(
            STUB_DBS="agent_legion_foo_bar agent_legion_test_foo_bar",
            RECREATE_WT="foo-bar",
            RECREATE_INITIALIZED=initialized or "",
        ),
        capture_output=True,
        text=True,
        cwd=env.main,
        timeout=_DEADLINE,
    )

    log = env.log.read_text()
    if refused:
        assert result.returncode == 1, result.stdout
        assert "拒绝删除派生 bucket" in result.stderr
        assert "foo-bar -> agent-legion-foo-bar" in result.stderr
        assert "stub delete_bucket" not in result.stdout
        assert "bucket-delete-under-lock" not in log
    else:
        assert result.returncode == 0, result.stderr
        assert "stub delete_bucket" in result.stdout
        assert "bucket-delete-under-lock" in log
    assert "dropdb agent_legion_foo_bar\n" in log
    # 别名 worktree 本身始终不动。
    assert (env.main / ".worktrees" / "foo-bar").is_dir()
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
    registered = env.add("foo_bar")
    # 同名登记只认已完成初始化的为资源主（#1084 R5）：补完成标记。
    (registered / ".worktree-initialized.ok").write_text("initialized_at=t\n", encoding="utf-8")

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


# --- 锁生命周期 vs 进程树（#1084 R5 熔断收窄）--------------------------------
#
# R4 的共同执行者登记机制在第 5 轮熔断中整体退役：收窄为「全部删除步骤在
# 持锁进程内同步执行」，锁内没有嵌套/后台执行者。残余语义：持有者被
# SIGKILL 时，在途的那一条命令会执行完（任何锁设计都无法撤回在途命令），
# 但没有任何孤儿进程继续推进后续步骤。


def test_killed_holder_leaves_no_orphan_progress(env: _Env) -> None:
    """clean 在删库中途被 SIGKILL：在途 dropdb 完成，但第二个库的删除永远
    不会发生（没有孤儿脚本继续推进），锁由竞争者正常回收。"""
    block = env.tmp / "block-release"
    scripts = env.runner()
    env.add("victim")
    clean = subprocess.Popen(
        ["bash", str(scripts / "clean-worktree.sh"), "victim", "--yes"],
        env=env.env(STUB_DBS=_VICTIM_DBS, BLOCK_RELEASE=str(block)),
        stdout=subprocess.PIPE,
        stderr=(env.tmp / "clean-killed.err").open("w"),
        text=True,
        cwd=env.main,
    )
    # clean 阻塞在第一个库的 dropdb（在途命令）
    _wait_for(
        lambda: "dropdb-blocked agent_legion_victim" in env.log.read_text(),
        "clean blocks in dropdb",
    )

    clean.kill()
    clean.wait(timeout=_DEADLINE)  # 回收 zombie：pid 判死生效

    block.touch()  # 在途 dropdb 完成（已声明的残余形态）
    _wait_for(
        lambda: "dropdb agent_legion_victim\n" in env.log.read_text(), "in-flight dropdb completes"
    )

    # 收窄语义的核心断言：没有孤儿进程推进后续步骤——第二个库的删除与
    # bucket 删除都不会发生。
    assert "dropdb agent_legion_test_victim" not in env.log.read_text()
    assert "bucket-delete-under-lock" not in env.log.read_text()

    # 锁按持有者 pid 判死回收：后续竞争者正常拿到锁并工作。
    contender, release_t = env.start_holder("t")
    release_t.touch()
    _wait_for(lambda: "acquired t" in env.events_lines(), "contender acquires after reclaim")
    assert contender.wait(timeout=_DEADLINE) == 0
    assert env.holders() == []
    # P3-2 兜底：竞争者跑完后再次断言无孤儿推进（覆盖迟到的孤儿写入）。
    assert "dropdb agent_legion_test_victim" not in env.log.read_text()
    assert "bucket-delete-under-lock" not in env.log.read_text()


# --- bucket 删除租约（#1084 熔断重估 R2 P1）----------------------------------
#
# R5 收窄后唯一的多步前台子进程是 bucket delete 的 uv/python：clean 被
# SIGKILL 时它成孤儿仍能继续多批删除，而锁 holder 只记 clean 的 pid。修法
# 是不换锁原语的逐批租约校验：clean 把 holder 文件路径（租约）与自身 pid
# 经 env 传给 python，python 在每个破坏性步骤前校验二者仍有效，失效自绝。

# clean 的 S3 heredoc 由 uv 桩转交真实 python（PYTHONPATH=runner worktree），
# 以下替身模块屏蔽 dotenv/boto3/botocore 与 repo 模块。
_LEASE_S3_MODULES = {
    "dotenv.py": "def load_dotenv(*a, **k):\n    return False\n",
    "botocore/__init__.py": "",
    "botocore/exceptions.py": (
        "class ClientError(Exception):\n"
        "    def __init__(self, response):\n"
        "        super().__init__('stub')\n"
        "        self.response = response\n"
        "class EndpointConnectionError(Exception):\n"
        "    pass\n"
    ),
    # head_bucket 成功（bucket 存在）；list 出一个对象；delete_objects 记录
    # 后阻塞到 BLOCK_RELEASE 出现（停在在途批次里）；delete_bucket 记录。
    "boto3.py": (
        "import os, time\n"
        "def _log(line):\n"
        "    with open(os.environ['STUB_LOG'], 'a', encoding='utf-8') as fh:\n"
        "        fh.write(line + '\\n')\n"
        "class _Paginator:\n"
        "    def paginate(self, Bucket):\n"
        "        return iter([{'Contents': [{'Key': 'k1', 'Size': 1}]}])\n"
        "class _Client:\n"
        "    def head_bucket(self, Bucket):\n"
        "        return None\n"
        "    def get_paginator(self, name):\n"
        "        return _Paginator()\n"
        "    def delete_objects(self, Bucket, Delete):\n"
        "        _log('delete_objects ' + ','.join(o['Key'] for o in Delete['Objects']))\n"
        "        block = os.environ.get('BLOCK_RELEASE')\n"
        "        if block:\n"
        "            while not os.path.exists(block):\n"
        "                time.sleep(0.05)\n"
        "    def delete_bucket(self, Bucket):\n"
        "        _log('delete_bucket ' + Bucket)\n"
        "def client(*a, **k):\n"
        "    return _Client()\n"
    ),
    "scripts/__init__.py": "",
    "scripts/seaweedfs_collection.py": (
        "class CollectionGuardError(Exception):\n    pass\n"
        "def leftover_volume_ids(*a):\n    return ()\n"
        "def manual_reclaim_command(*a):\n    return ''\n"
        "def resolve_master_url(*a):\n    return None\n"
    ),
    "server/__init__.py": "",
    "server/app/__init__.py": "",
    "server/app/storage.py": (
        "class _S:\n"
        "    region = 'us-east-1'\n"
        "    endpoint_url = None\n"
        "    access_key = None\n"
        "    secret_key = None\n"
        "def load_s3_settings():\n"
        "    return _S()\n"
    ),
}


def _install_lease_bucket_fakes(env: _Env, worktree: Path) -> None:
    for rel, content in _LEASE_S3_MODULES.items():
        target = worktree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _exe(env.bin / "uv", f'#!/usr/bin/env bash\nexec "{sys.executable}" -\n')


def test_bucket_delete_orphan_self_terminates_when_lease_lost(env: _Env) -> None:
    """clean 在 bucket delete 中途被 SIGKILL、竞争者回收锁（=同名 init 进入
    临界区重建 bucket）后，孤儿删除进程在下一个破坏性步骤前的租约校验失败
    并自绝——新实例的 bucket 不会被删。"""
    scripts = env.runner()
    env.add("victim")
    _install_lease_bucket_fakes(env, scripts.parent)
    block = env.tmp / "block-release"
    clean_err = env.tmp / "clean-lease.err"
    clean = subprocess.Popen(
        ["bash", str(scripts / "clean-worktree.sh"), "victim", "--yes"],
        env=env.env(STUB_DBS="", BLOCK_RELEASE=str(block)),
        stdout=subprocess.PIPE,
        stderr=clean_err.open("w"),
        text=True,
        cwd=env.main,
    )
    # 删除进程停在 delete_objects 批次里（在途命令）
    _wait_for(lambda: "delete_objects" in env.log.read_text(), "delete_objects in flight")

    # 杀 clean 并回收 zombie（pid 判死生效）；竞争者在孤儿删除仍在途时回收
    # 锁——覆盖「锁易主时在途删除未结束」的窗口（旧用例在在途命令完成后才
    # 启动竞争者，没覆盖到；此处先拿锁、后放行在途批次）。
    clean.kill()
    clean.wait(timeout=_DEADLINE)
    contender, release_t = env.start_holder("t")
    release_t.touch()
    _wait_for(
        lambda: "acquired t" in env.events_lines(),
        "contender reclaims lock while delete in flight",
    )
    assert contender.wait(timeout=_DEADLINE) == 0

    # 放走在途批次：下一破坏性步骤（delete_bucket）前的租约校验必须失败。
    block.touch()
    _wait_for(lambda: "租约失效" in clean_err.read_text(), "orphan aborts on lease check")

    assert "delete_bucket" not in env.log.read_text()
    assert clean.returncode == -9
    assert env.holders() == []
