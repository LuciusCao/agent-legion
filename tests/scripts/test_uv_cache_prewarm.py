"""Unit tests for scripts/uv_cache_prewarm.py（issue #1186 重写后的落位协议）。

pytest 直测 helper（tmp_path 合成 worktree/基准布局）：协议语义在
rename(2)/cp 原语级断言，不依赖宿主机进程表、pid 或固定时长。
init 侧集成用例（helper 被调用、失败不 fail-init）留在
test_init_worktree_guard.py。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.uv_cache_prewarm import STAGING_NAME, main, prewarm

pytestmark = pytest.mark.no_db


def _seed_base_cache(base: Path, marker: str = "cached-wheel\n") -> Path:
    entry = base / ".uv-cache/wheels-v6/marker"
    entry.parent.mkdir(parents=True)
    entry.write_text(marker)
    return base / ".uv-cache"


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
    assert not (worktree / STAGING_NAME).exists()


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
    assert not (worktree / STAGING_NAME).exists()


def test_skips_when_base_has_no_cache(tmp_path: Path) -> None:
    """基准无 .uv-cache（本机第一个 worktree）时静默跳过——冷启动合法。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    base.mkdir()

    assert prewarm(worktree, base) == "skipped-no-base"

    assert not (worktree / ".uv-cache").exists()
    assert not (worktree / STAGING_NAME).exists()


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
    assert not (worktree / STAGING_NAME).exists()


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
    assert not (worktree / f".uv-cache/{STAGING_NAME}").exists()
    # 自己的克隆（中转目录）已丢弃。
    assert not (worktree / STAGING_NAME).exists()


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
    assert not (worktree / STAGING_NAME).exists()


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
    assert not (worktree / STAGING_NAME).exists()


def test_staging_self_cleaned_at_entry_even_on_skip(tmp_path: Path) -> None:
    """自洁：入口无条件清空中转目录——上次中断（含 SIGKILL）的残留垃圾
    即使本次走跳过路径也被回收。"""
    worktree = tmp_path / "worktree"
    garbage = worktree / f"{STAGING_NAME}/leftover"
    garbage.mkdir(parents=True)
    (garbage / "junk").write_text("junk\n")
    base = tmp_path / "base"
    base.mkdir()  # 无 cache：走 skipped-no-base 早退路径

    assert prewarm(worktree, base) == "skipped-no-base"

    assert not (worktree / STAGING_NAME).exists()


def test_main_entry_without_base_arg_is_noop(tmp_path: Path) -> None:
    """无 BASE 参数（init 侧已挡，此处钉 no-op 语义）：直接返回 0。"""
    assert main(["uv_cache_prewarm.py"]) == 0
