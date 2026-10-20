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
        self,
        script: Path,
        *args: str,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            text=True,
            env=_script_env(self, extra_env),
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
    # 初始化完成标记在锁内写入（#1084 R5：删除护栏据此判定同名登记是否为
    # 资源主）；文件名与 worktree-names-lib.sh 的 WORKTREE_INITIALIZED_MARKER
    # 常量一致。
    marker = target / ".worktree-initialized.ok"
    assert marker.read_text(encoding="utf-8").startswith("initialized_at=")


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
    # head_bucket 以状态文件模拟「等锁期间 init 补建了 bucket」；
    # STUB_BUCKET_FAIL 指向的文件存在时模拟 endpoint 故障（探测 fail-closed
    # 用例）。
    "boto3.py": (
        "import os\n"
        "from botocore.exceptions import ClientError, EndpointConnectionError\n"
        "class _Paginator:\n"
        "    def paginate(self, Bucket):\n"
        "        return iter([{}])\n"
        "class _Client:\n"
        "    def head_bucket(self, Bucket):\n"
        "        fail = os.environ.get('STUB_BUCKET_FAIL')\n"
        "        if fail and os.path.exists(fail):\n"
        "            raise EndpointConnectionError('stub endpoint down')\n"
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
    assert "状态在确认/等锁期间已变化" in result.stderr
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
    # --yes 自动化语义：计划外资源打印警告后继续（#1084 P1-b）
    assert "计划外" in result.stderr


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
    # --yes 自动化语义：计划外资源打印警告后继续（#1084 P1-b）
    assert "计划外" in result.stderr


# --- 探测/指纹采集失败语义（#1084 codex R3 P1）-------------------------------
#
# 同族新形态：「探测/指纹采集的失败被吞成空状态继续推进」。三处修复：
# stat 方言分流（GNU 的 -f 语义不同，错误回退会把动态文件系统计数混进
# 指纹）、bucket/db 探测失败 fail-closed（只有成功探测后的空结果才算
# 「不存在」）。

# stat 方言仿真桩（STUB_STAT_DIALECT=gnu|bsd）：只暴露所选方言的接口，输出
# 一律由本机真 stat 翻译产生——保证两种方言下同一实例指纹一致、重建可区分。
_STAT_STUB = r"""#!/usr/bin/env bash
REAL=/usr/bin/stat
[[ -x "$REAL" ]] || REAL=/bin/stat
if "$REAL" --version >/dev/null 2>&1; then HOST=gnu; else HOST=bsd; fi
# emit <file|dir> <路径>：按指纹语义用本机真 stat 产出等价输出
emit() {
    if [[ "$HOST" == gnu ]]; then
        if [[ "$1" == file ]]; then "$REAL" -c '%d:%i:%Z:%W' "$2"; else "$REAL" -c '%d:%i:%W' "$2"; fi
    else
        if [[ "$1" == file ]]; then "$REAL" -f '%d:%i:%c:%B' "$2"; else "$REAL" -f '%d:%i:%B' "$2"; fi
    fi
}
case "${STUB_STAT_DIALECT:?}" in
  gnu)
    case "${1:-}" in
      --version) echo "stat (GNU coreutils) 9.1-stub"; exit 0 ;;
      -c) case "$2" in
            '%d:%i:%Z:%W') emit file "$3" ;;
            '%d:%i:%W')    emit dir "$3" ;;
            *) echo "stat stub: 未仿真 GNU 格式 $2" >&2; exit 1 ;;
          esac ;;
      # GNU 语义：-f 是「查文件系统」标志——格式串被当作不存在的路径报错，
      # 但仍对真实路径往 stdout 打印动态文件系统计数（随每次调用变化，忠实
      # 复现「错误回退把动态计数混进指纹」的缺陷形态）
      -f) count_file="${STUB_LOG}.statcount"
          n=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 ))
          echo "$n" > "$count_file"
          echo "  File: $3  ID: 0  Namelen: 255  Available blocks: $n"
          echo "stat: cannot read file system information for '$2': No such file or directory" >&2
          exit 1 ;;
      *) echo "stat stub: 未仿真参数 $*" >&2; exit 1 ;;
    esac ;;
  bsd)
    case "${1:-}" in
      --version) echo "stat: illegal option" >&2; exit 1 ;;
      -f) case "$2" in
            '%d:%i:%c:%B') emit file "$3" ;;
            '%d:%i:%B')    emit dir "$3" ;;
            *) echo "stat stub: 未仿真 BSD 格式 $2" >&2; exit 1 ;;
          esac ;;
      *) echo "stat stub: 未仿真参数 $*" >&2; exit 1 ;;
    esac ;;
esac
"""


def _install_stat_stub(repo: _Repo) -> None:
    _exe(repo.bin / "stat", _STAT_STUB)


@pytest.mark.parametrize("dialect", ["bsd", "gnu"])
def test_identity_fingerprint_stable_under_stat_dialect(repo: _Repo, dialect: str) -> None:
    """指纹在两种 stat 方言下格式稳定：未变化的实例两次快照一致，清理正常完成。"""
    runner = repo.add("runner")
    repo.install_scripts(runner)
    victim = repo.add("victim")
    _install_stat_stub(repo)

    result = repo.run(
        runner / "scripts" / "clean-worktree.sh",
        "victim",
        "--yes",
        extra_env={"STUB_STAT_DIALECT": dialect},
    )

    assert result.returncode == 0, result.stderr
    assert not victim.exists()
    assert "状态在确认/等锁期间已变化" not in result.stderr
    assert "收尾清理结束" in result.stdout


@pytest.mark.parametrize("dialect", ["bsd", "gnu"])
def test_rebuild_detected_under_stat_dialect(repo: _Repo, dialect: str) -> None:
    """P1-1 的替换识别在两种 stat 方言下都生效（确认窗口内同名重建→拒绝）。"""
    runner = repo.add("runner")
    repo.install_scripts(runner)
    victim_path = repo.add("victim")
    _exe(repo.bin / "psql", _PSQL_DB_PRESENT_STUB)
    _install_stat_stub(repo)

    run = _ScriptRun(
        repo,
        runner / "scripts" / "clean-worktree.sh",
        ("victim",),
        extra_env={"STUB_STAT_DIALECT": dialect},
    )
    run.wait_stdout("待删除: agent_legion_victim")
    repo._git("-C", str(repo.main), "commit", "-q", "--allow-empty", "-m", "advance")
    repo._git("-C", str(repo.main), "worktree", "remove", str(victim_path))
    repo._git("-C", str(repo.main), "worktree", "add", "-q", "-b", "rebuild", str(victim_path))
    run.write_stdin("victim\n")
    result = run.finish()

    assert result.returncode == 1
    assert "状态在确认/等锁期间已变化" in result.stderr
    assert victim_path.is_dir()
    assert "dropdb" not in repo.log.read_text(encoding="utf-8")


def test_clean_aborts_when_plan_bucket_probe_fails(repo: _Repo) -> None:
    """首次 bucket probe 失败（endpoint/鉴权/python 故障）→ fail-closed：
    不把「探测失败」当「不存在」推进，不确认、不动 worktree/库。"""
    runner = repo.add("runner")
    repo.install_scripts(runner)
    victim = repo.add("victim")
    _exe(repo.bin / "uv", "#!/usr/bin/env bash\nexit 1\n")

    result = repo.run(runner / "scripts" / "clean-worktree.sh", "victim", "--yes")

    assert result.returncode == 1
    assert "bucket 探测失败" in result.stderr
    assert victim.is_dir()
    assert "dropdb" not in repo.log.read_text(encoding="utf-8")


def test_clean_aborts_when_locked_bucket_reprobe_fails(repo: _Repo) -> None:
    """锁内重探测失败 → fail-closed：不凭锁前「不存在」跳过，也不盲删。"""
    runner = repo.add("runner")
    repo.install_scripts(runner)
    repo.add("victim")
    _install_bucket_fakes(repo, runner)
    bucket_ready = repo.tmp / "bucket-ready"
    bucket_ready.write_text("ready\n", encoding="utf-8")  # plan 与锁内 probe 都能见到 bucket
    bucket_fail = repo.tmp / "bucket-fail"
    sleeper = _hold_derived_name_lock(repo)

    run = _ScriptRun(
        repo,
        runner / "scripts" / "clean-worktree.sh",
        ("victim", "--yes"),
        extra_env={
            "STUB_BUCKET_READY": str(bucket_ready),
            "STUB_BUCKET_FAIL": str(bucket_fail),
        },
    )
    try:
        run.wait_stderr("派生名锁被占用")
        # 等锁期间 endpoint 故障：锁内重探测返回失败而非空
        bucket_fail.write_text("down\n", encoding="utf-8")
    finally:
        # kill 后必须 wait 回收：zombie 子进程 kill -0 仍判活（#1251）。
        sleeper.kill()
        sleeper.wait()
    result = run.finish()

    assert result.returncode == 1
    assert "锁内派生 bucket 重探测失败" in result.stderr
    assert "delete_bucket" not in repo.log.read_text(encoding="utf-8")


# psql 桩：库详情查询连接级失败（exit 1）——「查询失败」不得记为「不存在」。
_PSQL_PROBE_FAIL_STUB = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >>"$STUB_LOG"
if [[ "$*" == *"select 1 from pg_roles"* ]]; then exit 0; fi
if [[ "$*" == *pg_database_size* ]]; then exit 1; fi
exit 0
"""

# psql 桩：状态文件出现后库详情查询才失败（模拟等锁期间数据库失联）。
_PSQL_FAILS_LATE_STUB = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >>"$STUB_LOG"
if [[ "$*" == *"select 1 from pg_roles"* ]]; then exit 0; fi
if [[ -f "$STUB_PSQL_FAIL" && "$*" == *pg_database_size* ]]; then exit 1; fi
exit 0
"""


def test_drop_db_aborts_when_probe_fails(repo: _Repo) -> None:
    """psql 探测失败 ≠ 库不存在：fail-closed，不打印「没有需要删除的库」。"""
    runner = repo.add("runner")
    repo.install_scripts(runner)
    _exe(repo.bin / "psql", _PSQL_PROBE_FAIL_STUB)

    result = repo.run(runner / "scripts" / "drop-worktree-db.sh", "victim", "--yes")

    assert result.returncode == 1
    assert "探测库" in result.stderr
    assert "没有需要删除的库" not in result.stdout
    assert "dropdb" not in repo.log.read_text(encoding="utf-8")


def test_drop_db_aborts_when_locked_reprobe_fails(repo: _Repo) -> None:
    """计划探测成功（无库）、锁内重探测失败 → fail-closed，不静默放行。"""
    runner = repo.add("runner")
    repo.install_scripts(runner)
    _exe(repo.bin / "psql", _PSQL_FAILS_LATE_STUB)
    psql_fail = repo.tmp / "psql-fail"
    sleeper = _hold_derived_name_lock(repo)

    run = _ScriptRun(
        repo,
        runner / "scripts" / "drop-worktree-db.sh",
        ("victim", "--yes"),
        extra_env={"STUB_PSQL_FAIL": str(psql_fail)},
    )
    try:
        run.wait_stderr("派生名锁被占用")
        psql_fail.write_text("down\n", encoding="utf-8")
    finally:
        sleeper.kill()
        sleeper.wait()
    result = run.finish()

    assert result.returncode == 1
    assert "探测库" in result.stderr
    assert "没有需要删除的库" not in result.stdout
    assert "dropdb" not in repo.log.read_text(encoding="utf-8")


# --- 熔断重估第 2 轮：校验-remove 同互斥 / 计划外资源重确认（#1084 codex）----

# git 包装：目标身份快照的成分读取（symbolic-ref）计数到第 2 次（=锁内最终
# 校验）后「上膛」；此后的第一次 worktree list 应答前先同名重建目标——模拟
# 「最终校验与 git worktree remove 之间插入裸 git worktree add」（裸 git
# 不入派生名锁协议）。状态经文件传递（每次调用都是新进程）。
_GIT_SWAP_WRAPPER = """#!/usr/bin/env bash
REAL="__REAL_GIT__"
if [[ "${3:-}" == "symbolic-ref" ]]; then
    n=$(( $(cat "$STUB_GIT_COUNT" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$STUB_GIT_COUNT"
    if [[ "$n" -eq 2 ]]; then
        echo "$2" > "$STUB_GIT_TARGET"
    fi
fi
if [[ "${1:-}" == "worktree" && "${2:-}" == "list" && -e "$STUB_GIT_TARGET" ]]; then
    target="$(cat "$STUB_GIT_TARGET")"
    rm -f "$STUB_GIT_TARGET"
    "$REAL" -C "$STUB_GIT_MAIN" worktree remove "$target" >/dev/null
    "$REAL" -C "$STUB_GIT_MAIN" worktree add -q -b rebuilt-in-window "$target" >/dev/null
fi
exec "$REAL" "$@"
"""


def test_clean_aborts_when_target_swapped_between_final_check_and_remove(repo: _Repo) -> None:
    """P1-a：锁内最终校验与 remove 之间插入同名 `git worktree add`（裸 git
    不拿锁）→ clean 必须中止，新实例与全部资源不动。"""
    runner = repo.add("runner")
    repo.install_scripts(runner)
    victim_path = repo.add("victim")
    _exe(repo.bin / "psql", _PSQL_DB_PRESENT_STUB)
    _exe(
        repo.bin / "git",
        _GIT_SWAP_WRAPPER.replace("__REAL_GIT__", _GIT),
    )

    result = repo.run(
        runner / "scripts" / "clean-worktree.sh",
        "victim",
        "--yes",
        extra_env={
            "STUB_GIT_COUNT": str(repo.tmp / "symref-count"),
            "STUB_GIT_TARGET": str(repo.tmp / "swap-target"),
            "STUB_GIT_MAIN": str(repo.main),
        },
    )

    assert result.returncode == 1
    assert "已变化" in result.stderr
    # 新实例（重建的 checkout 与其分支）未被误删，派生库未被碰。
    assert victim_path.is_dir()
    assert "dropdb" not in repo.log.read_text(encoding="utf-8")
    ref = subprocess.run(
        [
            _GIT,
            "-C",
            str(repo.main),
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/rebuilt-in-window",
        ],
        env={**_git_env(repo.tmp), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert ref.returncode == 0


def test_clean_aborts_interactively_when_locked_probe_finds_unplanned_dbs(repo: _Repo) -> None:
    """P1-b：计划为空（无库无 bucket），等锁期间同名 init 建了库——锁内重
    探测发现计划外资源，交互模式必须中止（资源从未展示、未确认）。"""
    runner = repo.add("runner")
    repo.install_scripts(runner)
    victim_path = repo.add("victim")
    _exe(repo.bin / "psql", _PSQL_DB_APPEARS_STUB)
    db_ready = repo.tmp / "db-ready"
    sleeper = _hold_derived_name_lock(repo)

    run = _ScriptRun(
        repo,
        runner / "scripts" / "clean-worktree.sh",
        ("victim",),
        extra_env={"STUB_DB_READY": str(db_ready)},
    )
    try:
        run.wait_stderr("派生名锁被占用")
        # init 在 clean 等锁期间建好了库（计划外）
        db_ready.write_text("ready\n", encoding="utf-8")
    finally:
        # kill 后必须 wait 回收：zombie 子进程 kill -0 仍判活（#1251）。
        sleeper.kill()
        sleeper.wait()
    result = run.finish()

    assert result.returncode == 1
    assert "计划外" in result.stderr
    assert victim_path.is_dir()  # 中止于任何删除之前
    assert "dropdb" not in repo.log.read_text(encoding="utf-8")


def test_drop_aborts_interactively_when_locked_probe_finds_unplanned_dbs(repo: _Repo) -> None:
    """P1-b 同族（drop 独立入口）：计划为空、锁内出现计划外库 → 交互中止；
    --yes 模式警告后继续删。"""
    runner = repo.add("runner")
    repo.install_scripts(runner)
    _exe(repo.bin / "psql", _PSQL_DB_APPEARS_STUB)

    for args, refused in [(("victim",), True), (("victim", "--yes"), False)]:
        repo.log.write_text("", encoding="utf-8")
        db_ready = repo.tmp / f"db-ready-{'yes' if not refused else 'ask'}"
        sleeper = _hold_derived_name_lock(repo)
        run = _ScriptRun(
            repo,
            runner / "scripts" / "drop-worktree-db.sh",
            args,
            extra_env={"STUB_DB_READY": str(db_ready)},
        )
        try:
            run.wait_stderr("派生名锁被占用")
            db_ready.write_text("ready\n", encoding="utf-8")
        finally:
            sleeper.kill()
            sleeper.wait()
        result = run.finish()

        if refused:
            assert result.returncode == 1
            assert "计划外" in result.stderr
            assert "dropdb" not in repo.log.read_text(encoding="utf-8")
        else:
            assert result.returncode == 0, result.stderr
            assert "计划外" in result.stderr  # --yes 打印警告继续
            assert "dropdb agent_legion_victim\n" in repo.log.read_text(encoding="utf-8")
