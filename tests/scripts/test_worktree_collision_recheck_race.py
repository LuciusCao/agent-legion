"""Final-recheck races for worktree cleanup (#1084, #1387)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.helpers.worktree_collision import (
    _GIT,
    _PSQL_DB_APPEARS_STUB,
    _PSQL_DB_PRESENT_STUB,
    _exe,
    _git_env,
    _hold_derived_name_lock,
    _Repo,
    _ScriptRun,
)
from tests.helpers.worktree_collision import (
    repo as repo,
)

pytestmark = pytest.mark.no_db

# --- 熔断重估第 2 轮：校验-remove 同互斥 / 计划外资源重确认（#1084 codex）----

# git 包装：目标路径经 STUB_GIT_TARGET 文件给出。第 STUB_GIT_FIRE_AT 次
# `worktree list`（最终校验的条目读）只落下阶段标记；紧随其后的身份 HEAD
# 读在应答前重建目标，确定性覆盖「porcelain 已读、最终指纹未读」窗口。
# STUB_GIT_SAME_BRANCH=1 时同分支同 HEAD 重建（porcelain 条目逐字节不变，
# 只有指纹成分能区分）。状态经文件传递（每次调用都是新进程）。
# 本测试布景下最终校验的条目读是第 6 次 porcelain（MAIN 解析、计划预检、
# 计划条目、锁内重查、分支解析之后）。注意（复验 P2，不修）：计数是硬编码
# 的——clean 在最终校验之前新增任何 porcelain 调用都会让注入点静默提前，
# 届时需同步调整 STUB_GIT_FIRE_AT。
_GIT_SWAP_WRAPPER = """#!/usr/bin/env bash
REAL="__REAL_GIT__"
if [[ "${1:-}" == "worktree" && "${2:-}" == "list" && -e "$STUB_GIT_TARGET" ]]; then
    n=$(( $(cat "$STUB_GIT_COUNT" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$STUB_GIT_COUNT"
    if [[ "$n" -eq "$STUB_GIT_FIRE_AT" ]]; then
        : > "$STUB_GIT_FINAL_CHECK"
    fi
fi
if [[ -e "$STUB_GIT_FINAL_CHECK" && -e "$STUB_GIT_TARGET" \
    && "${1:-}" == "-C" && "${3:-}" == "rev-parse" && "${4:-}" == "HEAD" ]]; then
    target="$(cat "$STUB_GIT_TARGET")"
    rm -f "$STUB_GIT_TARGET"
    # 保活旧目录 inode 到重建完成，避免文件系统立刻复用 inode、叠加秒级
    # ctime/birthtime 粒度后让新旧指纹偶然相同（#1387）。
    exec 9<"$target"
    if [[ "$STUB_GIT_SAME_BRANCH" == "1" ]]; then
        branch="$("$REAL" -C "$target" symbolic-ref --short -q HEAD)"
        "$REAL" -C "$STUB_GIT_MAIN" worktree remove "$target" >/dev/null
        "$REAL" -C "$STUB_GIT_MAIN" worktree add -q "$target" "$branch" >/dev/null
    else
        "$REAL" -C "$STUB_GIT_MAIN" worktree remove "$target" >/dev/null
        "$REAL" -C "$STUB_GIT_MAIN" worktree add -q -b rebuilt-in-window "$target" >/dev/null
    fi
fi
exec "$REAL" "$@"
"""


def _run_clean_with_swap_hook(
    repo: _Repo, victim_path: Path, same_branch: bool
) -> subprocess.CompletedProcess[str]:
    runner = repo.add("runner")
    repo.install_scripts(runner)
    _exe(repo.bin / "psql", _PSQL_DB_PRESENT_STUB)
    _exe(repo.bin / "git", _GIT_SWAP_WRAPPER.replace("__REAL_GIT__", _GIT))
    (repo.tmp / "swap-target").write_text(str(victim_path), encoding="utf-8")
    return repo.run(
        runner / "scripts" / "clean-worktree.sh",
        "victim",
        "--yes",
        extra_env={
            "STUB_GIT_COUNT": str(repo.tmp / "porcelain-count"),
            "STUB_GIT_TARGET": str(repo.tmp / "swap-target"),
            "STUB_GIT_MAIN": str(repo.main),
            "STUB_GIT_FIRE_AT": "6",
            "STUB_GIT_FINAL_CHECK": str(repo.tmp / "final-entry-read"),
            "STUB_GIT_SAME_BRANCH": "1" if same_branch else "0",
        },
    )


def test_clean_aborts_when_target_swapped_between_final_check_and_remove(repo: _Repo) -> None:
    """P1-a：最终 porcelain 与指纹读之间插入同名 `git worktree add`（裸 git
    不拿锁，重建为不同分支）→ clean 必须中止，新实例与全部资源不动。"""
    victim_path = repo.add("victim")

    result = _run_clean_with_swap_hook(repo, victim_path, same_branch=False)

    assert result.returncode == 1
    assert "已变化" in result.stderr
    assert not (repo.tmp / "swap-target").exists(), result.stderr
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
    assert ref.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_clean_aborts_when_identical_branch_rebuild_slips_past_porcelain(repo: _Repo) -> None:
    """复验 P2：同分支同 HEAD 的 remove+add 重建（porcelain 条目逐字节不变）
    落在最终校验窗口——条目比对必然放过，必须由最后读的指纹比对抓住。"""
    victim_path = repo.add("victim")
    victim_branch = subprocess.run(
        [_GIT, "-C", str(victim_path), "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True,
        text=True,
        env={**_git_env(repo.tmp), "PATH": "/usr/bin:/bin"},
        check=True,
    ).stdout.strip()

    result = _run_clean_with_swap_hook(repo, victim_path, same_branch=True)

    assert result.returncode == 1
    assert "已变化" in result.stderr
    assert not (repo.tmp / "swap-target").exists(), result.stderr
    # 新实例未被误删：目录仍在，且仍检出原分支（同名同 HEAD 重建）。
    assert victim_path.is_dir()
    assert "dropdb" not in repo.log.read_text(encoding="utf-8")
    assert (
        subprocess.run(
            [_GIT, "-C", str(victim_path), "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            env={**_git_env(repo.tmp), "PATH": "/usr/bin:/bin"},
            check=True,
        ).stdout.strip()
        == victim_branch
    )


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
