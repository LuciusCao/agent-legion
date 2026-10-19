"""Derived-name collision guards for init/clean/drop worktree scripts (#950).

Worktree name -> DB name (non ``[a-zA-Z0-9_]`` folded to ``_``) and bucket
name (lowercased, non ``[a-z0-9-]`` folded to ``-``) is not injective, so two
worktrees can silently share one database/bucket and cleaning one drops the
other's. The scripts now refuse when another still-present worktree derives
the same name.

Tests build a real temporary git repo with real ``git worktree add``
worktrees (so ``git worktree list --porcelain`` parsing is exercised for
real), copy the scripts into a worktree, and stub ``psql``/``uv`` on a
restricted PATH: no real database or bucket is touched.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = (
    "init-worktree.sh",
    "clean-worktree.sh",
    "drop-worktree-db.sh",
    "worktree-names-lib.sh",
    "ensure-s3-bucket.py",
)

_GIT = shutil.which("git")

# psql 桩：记录每次调用；所有探测都查不到库（安全 no-op 路径）。
_PSQL_STUB = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >>"$STUB_LOG"
exit 0
"""

# uv 桩：init 的 vault key 生成拿到一行输出；clean 的 S3 heredoc 走
# 「endpoint 不可达」降级分支。
_UV_STUB = """#!/usr/bin/env bash
printf 'uv %s\\n' "$*" >>"$STUB_LOG"
echo "stub-vault-master-key"
"""


# dropdb 桩：记录每次调用（正常路径不该碰到真实数据库）。
_DROPDB_STUB = """#!/usr/bin/env bash
printf 'dropdb %s\\n' "$*" >>"$STUB_LOG"
exit 0
"""


def _exe(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _git_env(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


class _Repo:
    def __init__(self, tmp_path: Path) -> None:
        assert _GIT is not None
        self.tmp = tmp_path.resolve()
        (self.tmp / "home").mkdir()
        self.main = self.tmp / "main"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "stub.log"
        self.log.write_text("", encoding="utf-8")
        # 只暴露 git 本身（包装脚本），不把 git 所在目录整个放上 PATH——
        # 那里可能同时有真 createdb/psql。
        _exe(self.bin / "git", f'#!/usr/bin/env bash\nexec "{_GIT}" "$@"\n')
        _exe(self.bin / "psql", _PSQL_STUB)
        _exe(self.bin / "dropdb", _DROPDB_STUB)
        _exe(self.bin / "uv", _UV_STUB)
        self._git("init", "-q", str(self.main))
        self._git("-C", str(self.main), "commit", "-q", "--allow-empty", "-m", "init")
        self._n = 0

    def _git(self, *args: str) -> None:
        env = {**_git_env(self.tmp), "PATH": "/usr/bin:/bin"}
        subprocess.run([_GIT, *args], check=True, capture_output=True, env=env)

    def add(self, name: str) -> Path:
        self._n += 1
        path = self.main / ".worktrees" / name
        self._git("-C", str(self.main), "worktree", "add", "-q", "-b", f"b{self._n}", str(path))
        return path

    def install_scripts(self, worktree: Path) -> None:
        scripts = worktree / "scripts"
        scripts.mkdir(exist_ok=True)
        for name in _SCRIPTS:
            shutil.copy(ROOT / "scripts" / name, scripts / name)

    def run(
        self, script: Path, *args: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **_git_env(self.tmp),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STUB_LOG": str(self.log),
        }
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd or self.main,
            timeout=60,
        )


@pytest.fixture
def repo(tmp_path: Path) -> _Repo:
    if _GIT is None:
        pytest.skip("git not available")
    return _Repo(tmp_path)


# (新 worktree 名, 已存在的 worktree 名, 撞上的派生资源)
_COLLIDING = [
    (
        "foo-bar",
        "foo_bar",
        ["agent_legion_foo_bar", "agent_legion_test_foo_bar", "agent-legion-foo-bar"],
    ),
    (
        "release-0-7-8",
        "release-0.7.8",
        [
            "agent_legion_release_0_7_8",
            "agent_legion_test_release_0_7_8",
            "agent-legion-release-0-7-8",
        ],
    ),
    # 只差大小写：开发库保留大小写不撞，测试库与 bucket 小写化后撞。
    ("feat-a", "Feat_A", ["agent_legion_test_feat_a", "agent-legion-feat-a"]),
]


# --- init-worktree.sh ---------------------------------------------------------


@pytest.mark.parametrize(("new", "existing", "resources"), _COLLIDING)
def test_init_rejects_derived_name_collision(
    repo: _Repo, new: str, existing: str, resources: list[str]
) -> None:
    repo.add(existing)
    target = repo.add(new)
    repo.install_scripts(target)
    (target / ".env").write_text("# stub env\n")

    result = repo.run(target / "scripts" / "init-worktree.sh")

    assert result.returncode == 1, result.stdout
    assert "派生出同名资源" in result.stderr
    for resource in resources:
        assert f"{existing} -> {resource}\n" in result.stderr
    # 只列真正撞上的资源（大小写场景下开发库不算冲突）。
    assert result.stderr.count(f"{existing} -> ") == len(resources)
    # fail-fast 在任何副作用之前：.env 未被改写，未调 uv / psql。
    assert (target / ".env").read_text() == "# stub env\n"
    assert repo.log.read_text(encoding="utf-8") == ""
    assert not (target / "deploy").exists()


def test_init_without_collision_initializes(repo: _Repo) -> None:
    repo.add("foo_bar")
    target = repo.add("foo-baz")
    repo.install_scripts(target)
    (target / ".env").write_text("# stub env\n")

    result = repo.run(target / "scripts" / "init-worktree.sh")

    assert result.returncode == 0, result.stderr
    env = (target / ".env").read_text()
    assert "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_foo_baz" in env
    assert "AGENT_LEGION_S3_BUCKET=agent-legion-foo-baz" in env


def test_init_ignores_prunable_worktree_whose_directory_is_gone(repo: _Repo) -> None:
    gone = repo.add("foo_bar")
    shutil.rmtree(gone)  # 仍在 worktree list 里（prunable），但已无使用方
    target = repo.add("foo-bar")
    repo.install_scripts(target)
    (target / ".env").write_text("# stub env\n")

    result = repo.run(target / "scripts" / "init-worktree.sh")

    assert result.returncode == 0, result.stderr
    assert "派生出同名资源" not in result.stderr


# --- clean-worktree.sh / drop-worktree-db.sh -------------------------------


@pytest.mark.parametrize(("victim", "survivor", "resources"), _COLLIDING)
def test_clean_refuses_when_another_worktree_shares_derived_names(
    repo: _Repo, victim: str, survivor: str, resources: list[str]
) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add(survivor)
    victim_path = repo.add(victim)

    result = repo.run(runner / "scripts" / "clean-worktree.sh", victim, "--yes")

    assert result.returncode == 1, result.stdout
    assert "拒绝清理" in result.stderr
    for resource in resources:
        assert f"{survivor} -> {resource}\n" in result.stderr
    # 在第 1 步之前拒绝：worktree 仍在，未碰库与 bucket。
    assert victim_path.is_dir()
    assert repo.log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(("victim", "survivor", "resources"), _COLLIDING)
def test_drop_db_refuses_when_another_worktree_shares_derived_names(
    repo: _Repo, victim: str, survivor: str, resources: list[str]
) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add(survivor)

    # victim 已被移除（独立调用 drop 的典型时机），survivor 仍在用同名库。
    result = repo.run(runner / "scripts" / "drop-worktree-db.sh", victim, "--yes")

    assert result.returncode == 1, result.stdout
    assert "拒绝删除派生库" in result.stderr
    assert f"{survivor} -> {resources[0]}\n" in result.stderr
    assert repo.log.read_text(encoding="utf-8") == ""


def test_clean_without_collision_proceeds(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add("foo_bar")
    victim_path = repo.add("foo-baz")

    result = repo.run(runner / "scripts" / "clean-worktree.sh", "foo-baz", "--yes")

    assert result.returncode == 0, result.stderr
    assert not victim_path.exists()
    assert "不存在（跳过）: agent_legion_foo_baz\n" in result.stdout
    assert "不存在（跳过）: agent_legion_test_foo_baz\n" in result.stdout
    assert "收尾清理结束" in result.stdout
    assert "派生出同名资源" not in result.stderr


def test_drop_db_without_collision_proceeds(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add("foo_bar")

    result = repo.run(runner / "scripts" / "drop-worktree-db.sh", "release-0.7.8", "--yes")

    assert result.returncode == 0, result.stderr
    assert "没有需要删除的库" in result.stdout


# --- 跨锁边界重验（#1084 codex R2 P1）---------------------------------------
#
# 「检查 → 行动」跨越派生名锁边界时，锁前的 probe/确认结果只是计划素材，
# 行动前必须在锁内重验：目标 worktree 的身份（防确认窗口内被移除后以同名
# 同路径重建）与派生库/bucket 的存在性（防等锁期间 init 补建后沿用陈旧的
# 「不存在」跳过删除，留下会被同名 worktree 复用的资源）。


def _script_env(repo: _Repo, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        **_git_env(repo.tmp),
        "PATH": f"{repo.bin}:/usr/bin:/bin",
        "STUB_LOG": str(repo.log),
    }
    env.update(extra or {})
    return env


class _ScriptRun:
    """Popen 包装：后台线程收集 stdout/stderr，主线程等 stderr 出现特定提示
    （等信号非等时长）后注入 stdin / 翻转桩状态，最后收割退出状态。"""

    def __init__(
        self,
        repo: _Repo,
        script: Path,
        args: tuple[str, ...],
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._proc = subprocess.Popen(
            ["bash", str(script), *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=repo.main,
            env=_script_env(repo, extra_env),
        )
        self._buf = {"out": b"", "err": b""}
        self._cond = threading.Condition()
        self._threads = []
        for stream, key in ((self._proc.stdout, "out"), (self._proc.stderr, "err")):
            thread = threading.Thread(target=self._drain, args=(stream, key), daemon=True)
            thread.start()
            self._threads.append(thread)

    def _drain(self, stream, key: str) -> None:
        # os.read 而不是 stream.read(4096)：后者会攒满 4096 字节才返回，
        # 短提示（如锁等待横幅）要等到 EOF 才现身。
        fd = stream.fileno()
        while chunk := os.read(fd, 4096):
            with self._cond:
                self._buf[key] += chunk
                self._cond.notify_all()

    def _wait_for(self, key: str, needle: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while needle.encode() not in self._buf[key]:
                remaining = deadline - time.monotonic()
                assert remaining > 0, (
                    f"未等到 {key} 提示 {needle!r}；已有输出: "
                    f"out={self._buf['out'].decode(errors='replace')!r} "
                    f"err={self._buf['err'].decode(errors='replace')!r}"
                )
                self._cond.wait(remaining)

    def wait_stderr(self, needle: str, timeout: float = 30.0) -> None:
        self._wait_for("err", needle, timeout)

    def wait_stdout(self, needle: str, timeout: float = 30.0) -> None:
        self._wait_for("out", needle, timeout)

    def write_stdin(self, data: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(data.encode())
        self._proc.stdin.flush()
        self._proc.stdin.close()

    def finish(self, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
        rc = self._proc.wait(timeout=timeout)
        for thread in self._threads:
            thread.join(timeout=5)
        return subprocess.CompletedProcess(
            (), rc, self._buf["out"].decode(), self._buf["err"].decode()
        )


def _hold_derived_name_lock(repo: _Repo) -> subprocess.Popen:
    """以存活 pid 占位派生名锁，扮演正在持锁补建资源的 init-worktree.sh。"""
    lock_dir = repo.main / ".git" / "worktree-derived-names.lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    sleeper = subprocess.Popen(["sleep", "60"])
    (lock_dir / f"holder-{sleeper.pid}-test").write_text(
        f"pid={sleeper.pid} 测试占位（模拟 init 持锁）\n", encoding="utf-8"
    )
    return sleeper


# psql 桩：目标库已存在（触发 clean 的交互确认窗口）。注意库详情查询里
# join 了 pg_roles，role 探测必须匹配完整的 "select 1 from pg_roles"。
_PSQL_DB_PRESENT_STUB = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >>"$STUB_LOG"
if [[ "$*" == *"select 1 from pg_roles"* ]]; then exit 0; fi
if [[ "$*" == *pg_database_size* ]]; then echo "agent_legion_dev|1 MB"; exit 0; fi
if [[ "$*" == *"select 1 from pg_database where"* ]]; then echo "1"; exit 0; fi
exit 0
"""

# psql 桩：状态文件出现前报告目标库不存在，出现后报告存在（模拟 init 在
# clean 等锁期间补建了派生库）。
_PSQL_DB_APPEARS_STUB = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >>"$STUB_LOG"
if [[ "$*" == *"select 1 from pg_roles"* ]]; then exit 0; fi
if [[ ! -f "$STUB_DB_READY" ]]; then exit 0; fi
if [[ "$*" == *pg_database_size* ]]; then echo "agent_legion_dev|1 MB"; exit 0; fi
if [[ "$*" == *"select 1 from pg_database where"* ]]; then echo "1"; exit 0; fi
exit 0
"""

# clean 的 S3 heredoc 由 uv 桩转交真实 python 执行（PYTHONPATH 指向 runner
# worktree），以下替身模块屏蔽 dotenv/boto3/botocore 与 repo 模块。
_FAKE_S3_MODULES = {
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
    # head_bucket 以状态文件模拟「等锁期间 init 补建了 bucket」。
    "boto3.py": (
        "import os\n"
        "from botocore.exceptions import ClientError\n"
        "class _Paginator:\n"
        "    def paginate(self, Bucket):\n"
        "        return iter([{}])\n"
        "class _Client:\n"
        "    def head_bucket(self, Bucket):\n"
        "        if not os.path.exists(os.environ['STUB_BUCKET_READY']):\n"
        "            raise ClientError({'Error': {'Code': '404'}})\n"
        "    def get_paginator(self, name):\n"
        "        return _Paginator()\n"
        "    def delete_objects(self, Bucket, Delete):\n"
        "        pass\n"
        "    def delete_bucket(self, Bucket):\n"
        "        with open(os.environ['STUB_LOG'], 'a', encoding='utf-8') as fh:\n"
        "            fh.write(f'delete_bucket {Bucket}\\n')\n"
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


def _install_bucket_fakes(repo: _Repo, worktree: Path) -> None:
    for rel, content in _FAKE_S3_MODULES.items():
        target = worktree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _exe(repo.bin / "uv", f'#!/usr/bin/env bash\nexec "{sys.executable}" -\n')


def test_clean_refuses_when_target_rebuilt_during_confirmation(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    victim_path = repo.add("victim")
    _exe(repo.bin / "psql", _PSQL_DB_PRESENT_STUB)

    run = _ScriptRun(repo, runner / "scripts" / "clean-worktree.sh", ("victim",))
    # bash 的 read -p 只在 stdin 是 tty 时打印提示；改用计划输出的「待删除」
    # 行作为到达确认窗口的信号（确认读取紧随其后，仍在取锁之前）。
    run.wait_stdout("待删除: agent_legion_victim")
    # 确认窗口内（取锁之前）：另一流程移除原 worktree 并以同名同路径重建，
    # 新实例 HEAD 已前进（重建通常基于更新后的 main）。
    repo._git("-C", str(repo.main), "commit", "-q", "--allow-empty", "-m", "advance")
    repo._git("-C", str(repo.main), "worktree", "remove", str(victim_path))
    repo._git("-C", str(repo.main), "worktree", "add", "-q", "-b", "rebuild", str(victim_path))
    run.write_stdin("victim\n")
    result = run.finish()

    assert result.returncode == 1
    assert "身份已变化" in result.stderr
    # 新实例未被误伤：worktree 目录与重建分支仍在，派生库未被删除。
    assert victim_path.is_dir()
    assert "dropdb" not in repo.log.read_text(encoding="utf-8")
    ref = subprocess.run(
        [_GIT, "-C", str(repo.main), "show-ref", "--verify", "--quiet", "refs/heads/rebuild"],
        env={**_git_env(repo.tmp), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert ref.returncode == 0


def test_clean_reprobes_db_created_during_lock_wait(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add("victim")
    _exe(repo.bin / "psql", _PSQL_DB_APPEARS_STUB)
    db_ready = repo.tmp / "db-ready"
    sleeper = _hold_derived_name_lock(repo)

    run = _ScriptRun(
        repo,
        runner / "scripts" / "clean-worktree.sh",
        ("victim", "--yes"),
        extra_env={"STUB_DB_READY": str(db_ready)},
    )
    try:
        run.wait_stderr("派生名锁被占用")
        # init 在 clean 等锁期间补建了派生库：锁前 probe 的「不存在」已陈旧。
        db_ready.write_text("ready\n", encoding="utf-8")
    finally:
        # kill 后必须 wait 回收：zombie 子进程 kill -0 仍判活（#1251 的
        # 已知残余），锁的占用者判死回收会因此永不触发。
        sleeper.kill()
        sleeper.wait()
    result = run.finish()

    assert result.returncode == 0, result.stderr
    assert "dropdb agent_legion_victim\n" in repo.log.read_text(encoding="utf-8")
    assert "已删除: agent_legion_victim" in result.stdout


def test_clean_reprobes_bucket_created_during_lock_wait(repo: _Repo) -> None:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add("victim")
    _install_bucket_fakes(repo, runner)
    bucket_ready = repo.tmp / "bucket-ready"
    sleeper = _hold_derived_name_lock(repo)

    run = _ScriptRun(
        repo,
        runner / "scripts" / "clean-worktree.sh",
        ("victim", "--yes"),
        extra_env={"STUB_BUCKET_READY": str(bucket_ready)},
    )
    try:
        run.wait_stderr("派生名锁被占用")
        # 同上：init 在 clean 等锁期间补建了派生 bucket。
        bucket_ready.write_text("ready\n", encoding="utf-8")
    finally:
        # kill 后必须 wait 回收：zombie 子进程 kill -0 仍判活（#1251 的
        # 已知残余），锁的占用者判死回收会因此永不触发。
        sleeper.kill()
        sleeper.wait()
    result = run.finish()

    assert result.returncode == 0, result.stderr
    assert "delete_bucket agent-legion-victim\n" in repo.log.read_text(encoding="utf-8")
    assert "已删除 S3 bucket: agent-legion-victim" in result.stdout
