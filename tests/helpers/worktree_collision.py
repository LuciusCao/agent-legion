"""Shared real-git and process fixtures for worktree collision tests."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

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
