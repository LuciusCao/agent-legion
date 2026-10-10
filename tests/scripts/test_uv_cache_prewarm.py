"""Unit tests for scripts/uv_cache_prewarm.py（issue #1186 重写后的落位协议）。

pytest 直测 helper（tmp_path 合成 worktree/基准布局）：协议语义在
rename(2)/cp/lstat 原语级断言；「死/活 pid」一律现场制造（spawn+reap /
spawn 存活子进程），不依赖宿主机进程表既有状态或固定时长。init 侧集成
用例（helper 被调用、失败不 fail-init）留在 test_init_worktree_guard.py。
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import scripts.uv_cache_prewarm as prewarm_mod
from scripts.uv_cache_prewarm import STAGING_PREFIX, main, prewarm

pytestmark = pytest.mark.no_db

HELPER = Path(__file__).resolve().parents[2] / "scripts" / "uv_cache_prewarm.py"


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


def test_symlink_staging_is_unlinked_never_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    # 残留 symlink 占住本进程的中转路径（如上一轮被手工/异常放置）；
    # 钉住 nonce 让预置路径恰好是本次调用的 staging。
    monkeypatch.setattr(prewarm_mod.secrets, "token_hex", lambda _n: "5ymlink0")
    (worktree / f"{STAGING_PREFIX}.{os.getpid()}-5ymlink0").symlink_to(victim)

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
    """入口判活清扫：死 pid（spawn+reap 现场制造）与本进程 pid（重入，含
    异 nonce 旧目录）的残留清掉；活跃 pid 与非数字 pid 段一律保留（失败
    方向 = 保留死重）。命名兼容：<pid>-<nonce> 新形态与旧协议无 nonce
    残留都在清扫面内。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    base.mkdir()  # 无 cache：走早退路径也要完成清扫
    with subprocess.Popen(["true"]) as proc:
        dead_pid = proc.pid
        proc.wait()
    dead = worktree / f"{STAGING_PREFIX}.{dead_pid}-dead0000"
    dead.mkdir()
    legacy = worktree / f"{STAGING_PREFIX}.{dead_pid}"  # 旧协议无 nonce 残留
    legacy.mkdir()
    own = worktree / f"{STAGING_PREFIX}.{os.getpid()}-0wn00000"
    own.mkdir()
    named = worktree / f"{STAGING_PREFIX}.notapid"
    named.mkdir()
    named_nonce = worktree / f"{STAGING_PREFIX}.notapid-x1"
    named_nonce.mkdir()
    # 活跃「他人」pid：spawn 存活子进程承载（断言期间必然存活，非时长假设）。
    alive_proc = subprocess.Popen(["sleep", "30"])
    try:
        alive = worktree / f"{STAGING_PREFIX}.{alive_proc.pid}-a11ve000"
        alive.mkdir()

        assert prewarm(worktree, base) == "skipped-no-base"

        assert not dead.exists()
        assert not legacy.exists()
        assert not own.exists()
        assert alive.is_dir()
        assert named.is_dir()
        assert named_nonce.is_dir()
    finally:
        alive_proc.terminate()
        alive_proc.wait()


def test_sweep_in_flight_pid_reuse_never_deletes_reuser_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """格 7 结构性关闭（#1194）构造性回归：判死后、_remove_path 执行前
    pid 被同 worktree 新 prewarm 复用——复用者用同 pid + 不同 nonce 的
    新目录开始克隆，该目录不落在被清扫路径上 ⇒ 在飞 rmtree 与复用者的
    cp 交叠也零误删；清扫只命中真正的死残留（旧 nonce 目录）。后半段
    以同一复用 pid 跑真实 prewarm，端到端钉住「nonce 不同 ⇒ 真实生成
    的 staging 路径永不相同」这一结构命题（非仅靠构造目录名演示）。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    base.mkdir()  # 无 cache：早退路径，聚焦 S0 清扫语义
    reused_pid = 987654  # 合成 pid：_pid_alive 全程被时序 mock，不触宿主进程表
    stale = worktree / f"{STAGING_PREFIX}.{reused_pid}-dead0000"
    (stale / "old-junk").mkdir(parents=True)
    active = worktree / f"{STAGING_PREFIX}.{reused_pid}-newc0ffe"

    alive_now = {"value": False}
    monkeypatch.setattr(prewarm_mod, "_pid_alive", lambda _pid: alive_now["value"])
    real_remove = prewarm_mod._remove_path

    def remove_with_in_flight_reuse(path: Path) -> None:
        # 判死之后、rmtree 执行前：pid 被复用，新进程开始克隆（活跃写入）。
        if path == stale:
            alive_now["value"] = True
            (active / "clone-in-flight").mkdir(parents=True)
        real_remove(path)

    monkeypatch.setattr(prewarm_mod, "_remove_path", remove_with_in_flight_reuse)

    assert prewarm(worktree, base) == "skipped-no-base"

    assert not stale.exists()  # 真正的死残留被回收
    assert (active / "clone-in-flight").is_dir()  # 复用者的活跃克隆零损失

    # 端到端结构钉（review M1）：以复用 pid 跑真实 prewarm，截获 cp 目的地
    # ——真实生成的 staging 必带 -<nonce> 段，且永不等于 stale/active 两路径。
    _seed_base_cache(base)
    monkeypatch.setattr(prewarm_mod, "_remove_path", real_remove)
    monkeypatch.setattr(os, "getpid", lambda: reused_pid)
    generated: list[str] = []

    def recording_cp(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        generated.append(Path(cmd[-1]).name)
        return _fake_cp_creates_staging(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_cp)

    assert prewarm(worktree, base) == "prewarmed"

    (generated_name,) = generated
    assert generated_name.startswith(f"{STAGING_PREFIX}.{reused_pid}-")
    assert generated_name.removeprefix(f"{STAGING_PREFIX}.{reused_pid}-")
    assert generated_name not in {stale.name, active.name}


def test_consecutive_calls_generate_distinct_staging_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """nonce 每次调用新生成（#1194 review N2）：同进程同 pid 下两次连续
    prewarm 的 staging 路径名必须不同且都带 -<nonce> 段——「同 pid 异
    nonce ⇒ 路径不同」对真实调用成立（截获 cp 目的地取证）。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)
    names: list[str] = []

    def recording_cp(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        names.append(Path(cmd[-1]).name)
        return _fake_cp_creates_staging(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_cp)

    assert prewarm(worktree, base) == "prewarmed"
    shutil.rmtree(worktree / ".uv-cache")  # 让第二次调用重新走克隆路径
    assert prewarm(worktree, base) == "prewarmed"

    assert len(names) == 2
    assert names[0] != names[1]  # 同 pid 下 nonce 段是唯一区分 ⇒ 必须不同
    for name in names:
        nonce = name.removeprefix(f"{STAGING_PREFIX}.{os.getpid()}-")
        assert len(nonce) == 8  # token_hex(4) = 8 字符


def test_sweep_huge_pid_suffix_treated_as_dead_not_warn_degraded(tmp_path: Path) -> None:
    """_pid_alive 补捕 OverflowError（#1194 顺手项）：残留后缀为超大数字
    （如 30 位）时 os.kill 抛 OverflowError 而非 OSError（reviewer 本机
    实证）——穿透 sweep 会让每次 prewarm 走 warn 降级直到手工删除；必须
    视为死 pid 正常清扫。合成数字远超 pid 上限，不依赖宿主进程表。"""
    assert prewarm_mod._pid_alive(int("9" * 30)) is False  # 单元级钉住

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    huge = worktree / f"{STAGING_PREFIX}.{'9' * 30}-dead0000"
    huge.mkdir()

    assert prewarm(worktree, base) == "skipped-no-base"

    assert not huge.exists()


def test_dirty_staging_after_failed_cleanup_degrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """清理失败加固（矩阵格 5，reviewer minor 1）：清理留下预存 staging
    时走 warn 降级，不让 cp 以「拷入」语义把 staging/.uv-cache 嵌套落位
    成 final。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)
    # 钉住 nonce 让预置目录恰好占住本次调用的 staging 路径。
    monkeypatch.setattr(prewarm_mod.secrets, "token_hex", lambda _n: "d1rty000")
    staging = worktree / f"{STAGING_PREFIX}.{os.getpid()}-d1rty000"
    (staging / "leftover").mkdir(parents=True)
    monkeypatch.setattr(prewarm_mod, "_remove_path", lambda _path: None)  # 清理失败

    assert prewarm(worktree, base) == "failed-staging-dirty"

    assert "清理失败" in capsys.readouterr().err
    assert not (worktree / ".uv-cache").exists()  # 无嵌套落位
    assert (staging / "leftover").is_dir()  # 预存内容原样（保留死重方向）
    assert not (staging / ".uv-cache").exists()  # cp 未执行


def test_sigterm_handler_is_restored_after_prewarm(tmp_path: Path) -> None:
    """helper 可被库式调用：prewarm 返回后 SIGTERM handler 必须恢复原值，
    不泄漏进程状态。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)
    before = signal.getsignal(signal.SIGTERM)

    assert prewarm(worktree, base) == "prewarmed"

    assert signal.getsignal(signal.SIGTERM) == before


_CHILD_BLOCKING_CP = """
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("prewarm_mod", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fake_run(cmd, **kwargs):
    (Path(cmd[-1]) / "partial").mkdir(parents=True)
    time.sleep(30)  # 阻塞在「克隆进行中」，给 SIGTERM 留窗口
    return subprocess.CompletedProcess(cmd, 0)


mod.subprocess.run = fake_run
print(mod.prewarm(Path(sys.argv[2]), Path(sys.argv[3])), flush=True)
"""


def test_sigterm_during_clone_runs_finally_cleanup(tmp_path: Path) -> None:
    """codex P2 第三轮：SIGTERM 默认动作不跑 finally——handler 转
    SystemExit(143) 后清理路径必须执行：真实 SIGTERM 投递给跑阻塞 cp 的
    helper 子进程，断言退出码 143 且中转目录被清（等信号非等时长）。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    base = tmp_path / "base"
    _seed_base_cache(base)

    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD_BLOCKING_CP, str(HELPER), str(worktree), str(base)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # 等信号：中转目录出现且已有部分内容，确认子进程在「克隆进行中」。
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any((p / "partial").exists() for p in worktree.glob(f"{STAGING_PREFIX}.*")):
                break
            assert proc.poll() is None, f"子进程提前退出: {proc.communicate()}"
            time.sleep(0.01)
        else:
            pytest.fail("子进程未在预算内开始克隆")
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert proc.returncode == 143  # 128+15：handler 的 sys.exit(143)
    assert _staging_leftovers(worktree) == []  # finally 已清
    assert not (worktree / ".uv-cache").exists()  # 未落位


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
