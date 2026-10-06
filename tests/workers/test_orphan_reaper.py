"""Regression tests for orphaned agent process-group reaping.

The executor spawns agents with start_new_session=True (own process group)
and records the pid (= pgid) in the execution dir. If the executor is
SIGKILLed it cannot run its own cleanup, so the supervisor reaps the
recorded groups after killing the executor.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.helpers import pid_is_running
from worker import orphan_reaper, proc_groups
from worker.orphan_reaper import reap_orphaned_agents
from worker.process_lifecycle import AGENT_PGID_FILENAME

pytestmark = pytest.mark.no_db


def _spawn_group(marker: str = "") -> subprocess.Popen[bytes]:
    code = "import time; time.sleep(60)"
    if marker:
        # argv 携带 agent 标记，模拟真实 agent 命令（--name agent-legion-<execution_id>）
        code += f"  # {marker}"
    return subprocess.Popen(
        [sys.executable, "-c", code],
        start_new_session=True,
    )


def _write_record(work_root: Path, execution_id: str, pid: int | str) -> Path:
    record_dir = work_root / execution_id
    record_dir.mkdir(parents=True)
    record = record_dir / AGENT_PGID_FILENAME
    record.write_text(str(pid), encoding="utf-8")
    return record


def test_reap_kills_recorded_process_group(tmp_path: Path) -> None:
    proc = _spawn_group(marker="agent-legion-exec-1")
    record = _write_record(tmp_path, "exec-1", proc.pid)
    try:
        reap_orphaned_agents(tmp_path, lambda _msg: None)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert proc.poll() is not None
    assert not record.exists()


def test_reap_skips_group_without_agent_marker(tmp_path: Path) -> None:
    """pgid 被 OS 复用后组内没有本 execution 的 agent 标记：不得误杀，只清记录。"""
    proc = _spawn_group()  # 无标记 —— 冒充复用了 pgid 的无关进程组
    record = _write_record(tmp_path, "exec-1", proc.pid)
    try:
        reap_orphaned_agents(tmp_path, lambda _msg: None)
        assert proc.poll() is None  # 未收到任何信号
        assert not record.exists()
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_reap_kills_code_path_group_with_trailing_marker(tmp_path: Path) -> None:
    """#186：code 路径的标记是 argv 尾部元素（build_sandbox_argv 注入，
    不是 agent 路径的 --name 形态）——reaper 同样识别并回收。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "agent-legion-exec-code-1"],
        start_new_session=True,
    )
    record = _write_record(tmp_path, "exec-code-1", proc.pid)
    try:
        reap_orphaned_agents(tmp_path, lambda _msg: None)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert proc.poll() is not None
    assert not record.exists()


def test_reap_kills_grandchildren_too(tmp_path: Path) -> None:
    """The whole group dies, not just the direct child (no orphaned grandchildren)."""
    marker = tmp_path / "grandchild-survived"
    pid_file = tmp_path / "grandchild.pid"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, time\n"
            f"g = subprocess.Popen(['/bin/sh', '-c', 'sleep 2; touch {marker}'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
            "time.sleep(60)  # agent-legion-exec-1\n",
        ],
        start_new_session=True,
    )
    _write_record(tmp_path, "exec-1", proc.pid)
    try:
        # The pid file must exist BEFORE reaping: the reaper kills the whole
        # group on sight, and racing it against the grandchild's spawn (as CI
        # just proved) makes the file never appear. Waiting here also proves
        # the grandchild actually started, which is what the kill must cover.
        _wait_for_file(pid_file, timeout=10)
        grandchild = int(pid_file.read_text().strip())
        reap_orphaned_agents(tmp_path, lambda _msg: None)
        proc.wait(timeout=10)
        # Watch the grandchild's PID exit instead of sleeping past its touch
        # deadline: once the PID is gone, a surviving shell can no longer
        # touch. Race-free under load, and ~2s faster than the old blind wait.
        deadline = time.monotonic() + 10.0
        while pid_is_running(grandchild):
            assert time.monotonic() < deadline, (
                f"grandchild {grandchild} survived the reaper; marker={marker}"
            )
            time.sleep(0.05)
    finally:
        # Group-targeted cleanup: killing the grandchild by PID after its own
        # death could hit a recycled PID; the pgid (== proc.pid, established by
        # start_new_session and still ours while the group has members) cannot
        # be misattributed.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, 9)
        proc.wait(timeout=5)

    assert not marker.exists()


def _wait_for_file(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.05)


def test_reap_ignores_garbage_and_esrch_records(tmp_path: Path) -> None:
    garbage = _write_record(tmp_path, "exec-1", "not-a-pid")
    stale = _write_record(tmp_path, "exec-2", 999_999_999)  # ESRCH

    reap_orphaned_agents(tmp_path)  # must not raise

    assert garbage.exists()  # 解析失败的记录保留，下次 stop 再试
    assert not stale.exists()  # ESRCH 忽略，记录正常清除


@pytest.mark.parametrize("bad_pgid", ["0", "1", "-3"])
def test_reap_rejects_dangerous_pgid_records(tmp_path: Path, bad_pgid: str) -> None:
    """pgid <= 1 的记录（半截/恶意写入）会把信号发给调用方自身进程组，
    必须按垃圾记录跳过：不发送信号、不崩溃、记录保留待人工检查。"""
    record = _write_record(tmp_path, "exec-1", bad_pgid)

    reap_orphaned_agents(tmp_path)  # must not raise, must not signal our own group

    assert record.exists()
    os.killpg(os.getpgrp(), 0)  # 我们所在进程组安然无恙


def test_reap_tolerates_empty_work_root(tmp_path: Path) -> None:
    reap_orphaned_agents(tmp_path / "missing")  # must not raise
    reap_orphaned_agents(tmp_path)


# --- #682：/proc 标记校验（精简镜像无 ps）+ 杀完收割 -------------------------


def _fake_proc_entry(
    root: Path, pid: int, pgid: int, argv: list[str], state: str = "S", start: int = 0
) -> None:
    entry = root / str(pid)
    entry.mkdir(parents=True, exist_ok=True)
    fields = " ".join(["0"] * 15 + [str(start), "0"])
    entry.joinpath("stat").write_text(
        f"{pid} (velites x) {state} 1 {pgid} {pgid} {fields}\n", encoding="utf-8"
    )
    entry.joinpath("cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))


def test_marker_check_reads_fake_proc_without_ps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_ps(*_a: object, **_k: object) -> None:
        raise AssertionError("ps must not be consulted when /proc exists")

    monkeypatch.setattr(proc_groups.subprocess, "run", _no_ps)
    _fake_proc_entry(tmp_path, 500, 500, ["velites", "--name", "agent-legion-exec-1"])
    _fake_proc_entry(tmp_path, 501, 500, ["bwrap", "--unshare-pid"])
    _fake_proc_entry(tmp_path, 600, 600, ["sleep", "60"])
    _fake_proc_entry(tmp_path, 700, 700, [], state="Z")  # zombie: empty cmdline

    assert proc_groups.group_has_marker(500, "agent-legion-exec-1", tmp_path)
    assert not proc_groups.group_has_marker(600, "agent-legion-exec-1", tmp_path)
    assert not proc_groups.group_has_marker(500, "agent-legion-exec-2", tmp_path)
    assert not proc_groups.group_has_marker(700, "agent-legion-exec-1", tmp_path)


def test_marker_check_falls_back_to_ps_without_proc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _ps(argv: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, " 500 velites --name agent-legion-exec-1\n", "")

    monkeypatch.setattr(proc_groups.subprocess, "run", _ps)

    assert proc_groups.group_has_marker(500, "agent-legion-exec-1", tmp_path / "none")
    assert calls == [["ps", "-axo", "pgid=,args="]]


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="needs Linux /proc")
def test_marker_check_uses_real_proc_when_ps_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """精简镜像实况：ps 不存在（OSError），/proc 路径仍能确认身份。"""

    def _missing_ps(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("ps")

    monkeypatch.setattr(proc_groups.subprocess, "run", _missing_ps)
    proc = _spawn_group(marker="agent-legion-exec-proc")
    try:
        deadline = time.monotonic() + 10
        while not proc_groups.group_has_marker(proc.pid, "agent-legion-exec-proc"):
            assert time.monotonic() < deadline, "marker never visible in /proc cmdline"
            time.sleep(0.05)
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_reap_collects_killed_group_members_that_are_our_children(tmp_path: Path) -> None:
    """杀完即收：被收养（此处为本进程直接子进程）的组成员不留僵尸。"""
    proc = _spawn_group(marker="agent-legion-exec-1")
    _write_record(tmp_path, "exec-1", proc.pid)
    messages: list[str] = []

    reap_orphaned_agents(tmp_path, messages.append)

    with pytest.raises(ChildProcessError):
        os.waitpid(proc.pid, os.WNOHANG)  # already wait()ed by the reaper — no zombie left
    # TERM 已杀死整组时，KILL 前的身份现证可能因组已空（ps 回退）而跳过 KILL
    assert messages[-1] == f"reaped orphaned agent process group {proc.pid} (collected 1)"


def test_reap_skips_record_naming_our_own_process_group(tmp_path: Path) -> None:
    record = _write_record(tmp_path, "exec-1", os.getpgrp())

    reap_orphaned_agents(tmp_path, lambda _msg: None)  # must not signal or wait our own group

    assert record.exists()


def test_marker_check_with_snapshot_rereads_live_membership(tmp_path: Path) -> None:
    """快照只缩小候选：成员已离组（现读 stat 的 pgid 不符）时不得据旧快照放行。"""
    _fake_proc_entry(tmp_path, 500, 500, ["velites", "--name", "agent-legion-exec-1"])
    members = proc_groups.pgid_members(tmp_path)
    assert members == {500: [500]}
    (tmp_path / "500" / "stat").write_text("500 (velites) S 1 900 900" + " 0" * 17, "utf-8")

    assert not proc_groups.group_has_marker(500, "agent-legion-exec-1", tmp_path, members)
    assert proc_groups.pgid_members(tmp_path / "none") is None


def test_reap_many_groups_waits_once_not_per_record(tmp_path: Path) -> None:
    """一次 executor 被杀会遗留大量记录：整批 TERM/KILL 只等一次，而非每条 1s。"""
    procs = [_spawn_group(marker=f"agent-legion-exec-{i}") for i in range(4)]
    for i, proc in enumerate(procs):
        _write_record(tmp_path, f"exec-{i}", proc.pid)
    try:
        started = time.monotonic()
        reap_orphaned_agents(tmp_path, lambda _msg: None)
        elapsed = time.monotonic() - started
    finally:
        for proc in procs:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, 9)
    assert elapsed < 3.5, elapsed  # per-record sleeps would take >= 4s
    for proc in procs:
        with pytest.raises(ChildProcessError):
            os.waitpid(proc.pid, os.WNOHANG)


# --- codex P1 on #895：每次 killpg 前现证身份，pgid 复用时不发信号 ----------------


def test_identity_pins_members_and_detects_pgid_reuse(tmp_path: Path) -> None:
    _fake_proc_entry(tmp_path, 500, 500, ["velites", "--name", "agent-legion-exec-1"], start=111)
    _fake_proc_entry(tmp_path, 501, 500, ["bwrap"], start=112)
    identity = proc_groups.group_identity(500, "agent-legion-exec-1", tmp_path)
    assert identity is not None
    assert identity.members == frozenset({(500, 111), (501, 112)})
    assert proc_groups.still_owned(identity, tmp_path)

    shutil.rmtree(tmp_path / "500")  # leader died of SIGTERM; pinned child remains
    assert proc_groups.still_owned(identity, tmp_path)  # SIGKILL must still reach the child

    shutil.rmtree(tmp_path / "501")
    _fake_proc_entry(tmp_path, 500, 500, ["unrelated"], start=999)  # pid/pgid recycled
    assert not proc_groups.still_owned(identity, tmp_path)


def _patch_reaper_proc(
    monkeypatch: pytest.MonkeyPatch, root: Path, signals: list[tuple[int, int]]
) -> None:
    real_identity, real_owned = proc_groups.group_identity, proc_groups.still_owned
    real_members, real_refresh = proc_groups.pgid_members, proc_groups.refresh_identity
    real_index = proc_groups.MemberIndex
    monkeypatch.setattr(proc_groups, "pgid_members", lambda: real_members(root))
    monkeypatch.setattr(proc_groups, "MemberIndex", lambda: real_index(root))
    monkeypatch.setattr(
        proc_groups,
        "group_identity",
        lambda pgid, marker, members=None: real_identity(pgid, marker, root),
    )
    monkeypatch.setattr(
        proc_groups, "still_owned", lambda identity, _root=None: real_owned(identity, root)
    )
    monkeypatch.setattr(
        proc_groups, "refresh_identity", lambda identity, snap: real_refresh(identity, snap, root)
    )
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))


def test_reap_skips_kill_when_pgid_recycled_during_term_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    _fake_proc_entry(proc_root, 500, 500, ["velites", "--name", "agent-legion-exec-1"], start=111)
    _write_record(tmp_path / "work", "exec-1", 500)
    signals: list[tuple[int, int]] = []
    _patch_reaper_proc(monkeypatch, proc_root, signals)

    def _recycle_during_wait(_seconds: float) -> None:
        # 原组在 TERM 等待期间整组退出，pid/pgid 500 被一个无关的新进程组复用
        shutil.rmtree(proc_root / "500")
        _fake_proc_entry(proc_root, 500, 500, ["innocent", "service"], start=555)

    monkeypatch.setattr(orphan_reaper.time, "sleep", _recycle_during_wait)
    messages: list[str] = []

    orphan_reaper.reap_orphaned_agents(tmp_path / "work", messages.append)

    assert signals == [(500, signal.SIGTERM)]  # SIGKILL never sent to the recycled group
    assert "skipped SIGKILL to process group 500: identity changed" in messages


def test_reap_sends_nothing_when_group_vanished_before_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    _fake_proc_entry(proc_root, 500, 500, ["velites", "--name", "agent-legion-exec-1"], start=111)
    _fake_proc_entry(proc_root, 600, 600, ["velites", "--name", "agent-legion-exec-2"], start=222)
    _write_record(tmp_path / "work", "exec-1", 500)
    _write_record(tmp_path / "work", "exec-2", 600)
    signals: list[tuple[int, int]] = []
    _patch_reaper_proc(monkeypatch, proc_root, signals)
    real_identity = proc_groups.group_identity

    def _identity_then_vanish(pgid: int, marker: str, members: object = None):
        identity = real_identity(pgid, marker, proc_root)
        if pgid == 500:  # 校验通过后、批量发信号前，该组退出且 pgid 被复用
            shutil.rmtree(proc_root / "500")
            _fake_proc_entry(proc_root, 500, 500, ["innocent"], start=777)
        return identity

    monkeypatch.setattr(proc_groups, "group_identity", _identity_then_vanish)
    monkeypatch.setattr(orphan_reaper.time, "sleep", lambda _s: None)

    orphan_reaper.reap_orphaned_agents(tmp_path / "work", lambda _m: None)

    assert (500, signal.SIGTERM) not in signals and (500, signal.SIGKILL) not in signals
    assert signals == [(600, signal.SIGTERM), (600, signal.SIGKILL)]


def test_reap_kills_term_ignoring_member_spawned_after_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex P1 R3：校验后才派生、忽略 TERM 的成员，在旧钉住成员全部退出
    （tini 立即收割、/proc 里消失）后仍须收到 SIGKILL。"""
    proc_root = tmp_path / "proc"
    _fake_proc_entry(proc_root, 500, 500, ["velites", "--name", "agent-legion-exec-1"], start=111)
    _write_record(tmp_path / "work", "exec-1", 500)
    signals: list[tuple[int, int]] = []
    _patch_reaper_proc(monkeypatch, proc_root, signals)
    real_identity = proc_groups.group_identity

    def _identity_then_spawn(pgid: int, marker: str, members: object = None):
        identity = real_identity(pgid, marker, proc_root)
        _fake_proc_entry(proc_root, 501, 500, ["bwrap", "--ignore-term"], start=300)  # post-pin
        return identity

    monkeypatch.setattr(proc_groups, "group_identity", _identity_then_spawn)
    monkeypatch.setattr(
        orphan_reaper.time, "sleep", lambda _s: shutil.rmtree(proc_root / "500")
    )  # pinned leader dies of TERM and is reaped at once; 501 ignores TERM

    orphan_reaper.reap_orphaned_agents(tmp_path / "work", lambda _m: None)

    assert signals == [(500, signal.SIGTERM), (500, signal.SIGKILL)]


# --- #904：多组批量时按组在各自 TERM 紧前刷新成员 ----------------------------


def test_member_index_sees_new_and_drops_gone_pids_incrementally(tmp_path: Path) -> None:
    _fake_proc_entry(tmp_path, 500, 500, ["velites"], start=1)
    _fake_proc_entry(tmp_path, 600, 600, ["velites"], start=2)
    index = proc_groups.MemberIndex(tmp_path)
    assert index.members_of(600) == {600: [600]}

    _fake_proc_entry(tmp_path, 601, 600, ["bwrap"], start=3)
    shutil.rmtree(tmp_path / "500")
    assert index.members_of(600) == {600: [600, 601]}
    assert index.members_of(500) == {500: []}
    assert proc_groups.MemberIndex(tmp_path / "none").members_of(500) == {500: []}


def test_member_index_reindexes_recycled_pid_by_starttime(tmp_path: Path) -> None:
    """#982：pid 在两次刷新之间退出并被复用（starttime 变化）——即使 pid 一直
    出现在列表里，也按新进程重新归组，不沿用缓存的旧 pgid。"""
    _fake_proc_entry(tmp_path, 500, 500, ["velites"], start=1)
    _fake_proc_entry(tmp_path, 600, 600, ["velites"], start=2)
    _fake_proc_entry(tmp_path, 700, 700, ["unrelated"], start=3)
    index = proc_groups.MemberIndex(tmp_path)
    assert index.members_of(600) == {600: [600]}

    # 700 退出、pid 回绕后被后序组 600 新派生的成员复用（中间没有刷新看到空档）
    _fake_proc_entry(tmp_path, 700, 600, ["bwrap", "--ignore-term"], start=99)
    assert index.members_of(600) == {600: [600, 700]}
    assert index.members_of(700) == {700: []}

    # 同一进程（starttime 不变）留在原组：不重复归组
    assert index.members_of(600) == {600: [600, 700]}
    assert index.members_of(500) == {500: [500]}


def test_reap_kills_recycled_pid_spawned_into_later_group_mid_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#982：多组批量中，后序组忽略 TERM 的新成员复用了一个入口时属于别组的
    pid——仍被钉住并收到 SIGKILL。"""
    proc_root = tmp_path / "proc"
    _fake_proc_entry(proc_root, 500, 500, ["velites", "--name", "agent-legion-exec-1"], start=111)
    _fake_proc_entry(proc_root, 600, 600, ["velites", "--name", "agent-legion-exec-2"], start=222)
    _fake_proc_entry(proc_root, 900, 900, ["unrelated"], start=5)
    _write_record(tmp_path / "work", "exec-1", 500)
    _write_record(tmp_path / "work", "exec-2", 600)
    signals: list[tuple[int, int]] = []
    _patch_reaper_proc(monkeypatch, proc_root, signals)

    later: list[int] = []

    def _killpg(pgid: int, sig: int) -> None:
        signals.append((pgid, sig))
        if sig == signal.SIGTERM and not later:  # 900 退出，pid 被后序组新成员复用
            later.append(600 if pgid == 500 else 500)
            _fake_proc_entry(proc_root, 900, later[0], ["bwrap", "--ignore-term"], start=333)

    monkeypatch.setattr(os, "killpg", _killpg)
    monkeypatch.setattr(
        orphan_reaper.time, "sleep", lambda _s: shutil.rmtree(proc_root / str(later[0]))
    )  # 后序组原钉住成员死于 TERM 并被立即收割；复用 pid 900 的成员忽略 TERM 存活

    orphan_reaper.reap_orphaned_agents(tmp_path / "work", lambda _m: None)

    assert signals[1] == (later[0], signal.SIGTERM)
    assert (later[0], signal.SIGKILL) in signals


def test_reap_kills_term_ignoring_member_spawned_into_later_group_mid_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多组批量：后序组在入口快照之后（前序组 TERM 时）才派生忽略 TERM 的成员，
    原钉住成员在等待期退出并被立即收割后，该成员仍须收到 SIGKILL。"""
    proc_root = tmp_path / "proc"
    _fake_proc_entry(proc_root, 500, 500, ["velites", "--name", "agent-legion-exec-1"], start=111)
    _fake_proc_entry(proc_root, 600, 600, ["velites", "--name", "agent-legion-exec-2"], start=222)
    _write_record(tmp_path / "work", "exec-1", 500)
    _write_record(tmp_path / "work", "exec-2", 600)
    signals: list[tuple[int, int]] = []
    _patch_reaper_proc(monkeypatch, proc_root, signals)

    later: list[int] = []  # 批次中后序的组（glob 顺序不定，按首个 TERM 判定）

    def _killpg(pgid: int, sig: int) -> None:
        signals.append((pgid, sig))
        if sig == signal.SIGTERM and not later:  # 批次已开始，后序组才派生新成员
            later.append(600 if pgid == 500 else 500)
            _fake_proc_entry(proc_root, later[0] + 1, later[0], ["bwrap", "--ignore-term"], start=3)

    monkeypatch.setattr(os, "killpg", _killpg)
    monkeypatch.setattr(
        orphan_reaper.time, "sleep", lambda _s: shutil.rmtree(proc_root / str(later[0]))
    )  # 后序组原钉住成员死于 TERM 并被立即收割；新成员忽略 TERM 存活

    orphan_reaper.reap_orphaned_agents(tmp_path / "work", lambda _m: None)

    assert signals[1] == (later[0], signal.SIGTERM)
    assert (later[0], signal.SIGKILL) in signals
