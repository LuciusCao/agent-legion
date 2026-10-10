"""Unit tests for scripts/uv_cache_prewarm.py（issue #1186 重写后的落位协议）。

pytest 直测 helper（tmp_path 合成 worktree/基准布局）：协议语义在
rename(2)/cp/lstat 原语级断言；「死/活 pid」一律现场制造（spawn+reap /
spawn 存活子进程），不依赖宿主机进程表既有状态或固定时长。init 侧集成
用例（helper 被调用、失败不 fail-init）留在 test_init_worktree_guard.py。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import scripts.uv_cache_prewarm as prewarm_mod
from scripts.uv_cache_prewarm import STAGING_PREFIX, main, prewarm

pytestmark = pytest.mark.no_db


def _seed_base_cache(base: Path, marker: str = "cached-wheel\n") -> Path:
    entry = base / ".uv-cache/wheels-v6/marker"
    entry.parent.mkdir(parents=True)
    entry.write_text(marker)
    return base / ".uv-cache"


def _staging_leftovers(worktree: Path) -> list[Path]:
    return list(worktree.glob(f"{STAGING_PREFIX}*"))


def test_prewarm_clones_base_cache(tmp_path: Path) -> None:
    """成功预暖：内容一致、落位为真实目录（非 symlink）、中转目录已清。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)

    assert prewarm(worktree, base) == "prewarmed"

    final = worktree / ".uv-cache"
    assert final.is_dir() and not final.is_symlink()
    assert (final / "wheels-v6/marker").read_text() == "cached-wheel\n"
    assert _staging_leftovers(worktree) == []


def test_skips_when_target_cache_exists(tmp_path: Path) -> None:
    """幂等：目标已有 .uv-cache 时不覆盖、不重复克隆。"""
    worktree = tmp_path / "worktree"
    existing = worktree / ".uv-cache/mine"
    existing.parent.mkdir(parents=True)
    existing.write_text("mine\n")
    base = tmp_path / "base"
    _seed_base_cache(base)

    assert prewarm(worktree, base) == "skipped-existing"

    assert existing.read_text() == "mine\n"
    assert not (worktree / ".uv-cache/wheels-v6").exists()
    assert _staging_leftovers(worktree) == []


def test_skips_when_base_has_no_cache(tmp_path: Path) -> None:
    """基准无 .uv-cache（本机第一个 worktree）时静默跳过——冷启动合法。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    base.mkdir()

    assert prewarm(worktree, base) == "skipped-no-base"

    assert not (worktree / ".uv-cache").exists()
    assert _staging_leftovers(worktree) == []


def test_dereferences_symlink_base(tmp_path: Path) -> None:
    """基准 .uv-cache 是 symlink 时解引用克隆实体目录：新 cache 必须是真实
    目录而非指向共享目标的 symlink（per-worktree 隔离），内容一致。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    shared = base / "shared-uv-cache/wheels-v6"
    shared.mkdir(parents=True)
    (shared / "marker").write_text("cached-wheel\n")
    (base / ".uv-cache").symlink_to("shared-uv-cache")

    assert prewarm(worktree, base) == "prewarmed"

    final = worktree / ".uv-cache"
    assert final.is_dir() and not final.is_symlink()
    assert (final / "wheels-v6/marker").read_text() == "cached-wheel\n"


def test_unresolvable_symlink_base_warns_and_skips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """基准 .uv-cache 是悬空 symlink：warn 跳过，不 fail、不落位。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (base / ".uv-cache").symlink_to("gone")

    assert prewarm(worktree, base) == "skipped-symlink"

    assert "解引用失败" in capsys.readouterr().err
    assert not (worktree / ".uv-cache").exists()
    assert _staging_leftovers(worktree) == []


def _fake_cp_creates_staging(
    cmd: list[str], **kwargs: object
) -> subprocess.CompletedProcess[bytes]:
    """模拟克隆成功：在 cp 的目的地（最后一个参数）造出已克隆内容。"""
    target = Path(cmd[-1])
    (target / "cloned-entry").mkdir(parents=True)
    return subprocess.CompletedProcess(cmd, 0)


def test_concurrent_loser_discards_own_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """并发落败：克隆窗口内兄弟已落位非空 .uv-cache——rename(2) 原子失败
    （EEXIST/ENOTEMPTY），中转目录被清、先到者 cache 原样、无嵌套产物。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        result = _fake_cp_creates_staging(cmd, **kwargs)
        # 兄弟在克隆窗口内完成落位。
        (worktree / ".uv-cache/sibling-entry").mkdir(parents=True)
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert prewarm(worktree, base) == "skipped-concurrent"

    assert "并发" in capsys.readouterr().err
    # 先到者 cache 原样保留：未被覆盖、无嵌套克隆产物。
    assert (worktree / ".uv-cache/sibling-entry").is_dir()
    assert not (worktree / ".uv-cache/cloned-entry").exists()
    assert not list(worktree.glob(f".uv-cache/{STAGING_PREFIX}*"))
    # 自己的克隆（中转目录）已丢弃。
    assert _staging_leftovers(worktree) == []


def test_interleaved_calls_never_land_a_partial_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex P1 回归钉：A 克隆进行中 B 完整落位，A 写完后再 rename——

    独立中转目录下 B 不会清空/续写 A 的目录，A 的 rename 只会原子失败
    （EEXIST）并丢弃自己的完整克隆：落位内容恒等于 B 的完整克隆，永不
    出现部分落位（文件集必须恰好等于基准 cache）。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    source = _seed_base_cache(base)
    (source / "archive-v0/pkg-a").mkdir(parents=True)
    (source / "archive-v0/pkg-a/__init__.py").write_text("a\n")
    expected = sorted(p.relative_to(source) for p in source.rglob("*"))

    pid_a = os.getpid()  # 真实存活：B 的判活清扫必须跳过 A 的中转目录
    pid_b = pid_a + 1_000_000  # 仅作 B 的目录后缀（self 短路，不判活）
    current = {"pid": pid_a}
    monkeypatch.setattr(os, "getpid", lambda: current["pid"])

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        dst = Path(cmd[-1])
        if current["pid"] == pid_b:
            shutil.copytree(source, dst)  # B 的克隆：一次写全
            return subprocess.CompletedProcess(cmd, 0)
        # A 的克隆：先写一半，让出窗口给 B 完整跑完，再续写完毕——
        # 复现 codex「A rename 在 B 写入/落位之后」的交错形态。
        shutil.copytree(source / "wheels-v6", dst / "wheels-v6")
        current["pid"] = pid_b
        assert prewarm(worktree, base) == "prewarmed"
        current["pid"] = pid_a
        # B 已落位但绝未触碰 A 的中转目录：A 续写仍落在自己的目录里。
        assert (dst / "wheels-v6/marker").read_text() == "cached-wheel\n"
        shutil.copytree(source, dst, dirs_exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert prewarm(worktree, base) == "skipped-concurrent"

    # 落位的是 B 的完整克隆：文件集恰好等于基准，无缺失、无 A 的污染。
    final = worktree / ".uv-cache"
    assert sorted(p.relative_to(final) for p in final.rglob("*")) == expected
    # 双方中转目录都被清（A 走 finally，B 落位后路径已不存在）。
    assert _staging_leftovers(worktree) == []


def test_empty_final_dir_is_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rename(2) 对**空目录**目标成功替换（docstring 钉住的语义）：并发
    `uv run` 在克隆窗口内刚建的空 cache 被完整 cache 整体换掉——无害
    且有益（空 cache 无任何可丢失内容）。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        result = _fake_cp_creates_staging(cmd, **kwargs)
        # 并发 uv 调用在「入口判定 → 落位」窗口内创建空 cache。
        (worktree / ".uv-cache").mkdir()
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert prewarm(worktree, base) == "prewarmed"

    # 空目录被中转目录整体替换：落位后是克隆内容而非空 cache。
    assert (worktree / ".uv-cache/cloned-entry").is_dir()


def test_clone_failure_cleans_staging_and_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """克隆 I/O 失败：半成品中转目录必须清掉不污染新 cache，warn 降级。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        (Path(cmd[-1]) / "partial-entry").mkdir(parents=True)
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert prewarm(worktree, base) == "failed-clone"

    assert "预暖克隆失败" in capsys.readouterr().err
    assert not (worktree / ".uv-cache").exists()
    assert _staging_leftovers(worktree) == []


def test_landing_failure_cleans_staging_and_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """落位失败（非 EEXIST/ENOTEMPTY 的 OSError）：中转目录清掉，warn 降级。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)
    monkeypatch.setattr(subprocess, "run", _fake_cp_creates_staging)

    def fake_rename(src: str, dst: str) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("scripts.uv_cache_prewarm.os.rename", fake_rename)

    assert prewarm(worktree, base) == "failed-landing"

    assert "预暖落位失败" in capsys.readouterr().err
    assert not (worktree / ".uv-cache").exists()
    assert _staging_leftovers(worktree) == []


def test_symlink_staging_is_unlinked_never_followed(tmp_path: Path) -> None:
    """codex P2：残留中转路径是指向目录的 symlink 时必须 unlink 而非
    rmtree 保留——cp 不得穿透写入链接目标（可为任意目录/其他 worktree），
    落位的 .uv-cache 必须是真实目录而非 symlink 本身。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "canary").write_text("untouched\n")
    # 残留 symlink 占住本进程的中转路径（如上一轮被手工/异常放置）。
    (worktree / f"{STAGING_PREFIX}.{os.getpid()}").symlink_to(victim)

    assert prewarm(worktree, base) == "prewarmed"

    # 链接目标零写入：canary 原样，无克隆内容穿透。
    assert (victim / "canary").read_text() == "untouched\n"
    assert list(victim.iterdir()) == [victim / "canary"]
    # 落位为真实目录、内容完整；symlink 残留已清。
    final = worktree / ".uv-cache"
    assert final.is_dir() and not final.is_symlink()
    assert (final / "wheels-v6/marker").read_text() == "cached-wheel\n"
    assert _staging_leftovers(worktree) == []


def test_sweep_removes_only_dead_pid_and_own_leftovers(tmp_path: Path) -> None:
    """入口判活清扫：死 pid（spawn+reap 现场制造）与本进程 pid（重入）的
    残留清掉；活跃 pid 与非数字后缀一律保留（失败方向 = 保留死重）。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    base.mkdir()  # 无 cache：走早退路径也要完成清扫
    with subprocess.Popen(["true"]) as proc:
        dead_pid = proc.pid
        proc.wait()
    dead = worktree / f"{STAGING_PREFIX}.{dead_pid}"
    dead.mkdir()
    own = worktree / f"{STAGING_PREFIX}.{os.getpid()}"
    own.mkdir()
    named = worktree / f"{STAGING_PREFIX}.notapid"
    named.mkdir()
    # 活跃「他人」pid：spawn 存活子进程承载（断言期间必然存活，非时长假设）。
    alive_proc = subprocess.Popen(["sleep", "30"])
    try:
        alive = worktree / f"{STAGING_PREFIX}.{alive_proc.pid}"
        alive.mkdir()

        assert prewarm(worktree, base) == "skipped-no-base"

        assert not dead.exists()
        assert not own.exists()
        assert alive.is_dir()
        assert named.is_dir()
    finally:
        alive_proc.terminate()
        alive_proc.wait()


def test_legacy_bare_staging_name_is_self_cleaned_on_skip(tmp_path: Path) -> None:
    """首版固定名残留（无 pid 后缀、glob 匹配不到）也在入口被 lstat 安全
    清扫——即使本次走跳过路径。"""
    worktree = tmp_path / "worktree"
    garbage = worktree / f"{STAGING_PREFIX}/leftover"
    garbage.mkdir(parents=True)
    (garbage / "junk").write_text("junk\n")
    base = tmp_path / "base"
    base.mkdir()

    assert prewarm(worktree, base) == "skipped-no-base"

    assert _staging_leftovers(worktree) == []


def test_main_entry_without_base_arg_is_noop(tmp_path: Path) -> None:
    """无 BASE 参数（init 侧已挡，此处钉 no-op 语义）：直接返回 0。"""
    assert main(["uv_cache_prewarm.py"]) == 0


def test_main_entry_never_raises_on_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """__main__ 兜底：prewarm 抛出任何意外也只 warn、退出码 0。"""
    monkeypatch.setattr(prewarm_mod, "prewarm", lambda *_args: 1 / 0)

    assert main(["uv_cache_prewarm.py", "/nonexistent-base"]) == 0

    assert "预暖异常" in capsys.readouterr().err
