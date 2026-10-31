from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_db


def test_wrap_probe_separates_outer_and_inner_bwrap_without_secret_reads() -> None:
    from scripts.stress.worker_resource_wrap_evidence import _WRAP_PROBE

    assert '"outer": outer' in _WRAP_PROBE
    assert '"cases": cases' in _WRAP_PROBE
    assert "strace" in _WRAP_PROBE
    expected_trace = (
        "trace=clone,clone3,capset,unshare,setuid,setgid,prctl,mount,umount2,setns,"
        "socket,bind,sendto,recvmsg,openat,write,setgroups"
    )
    assert f'trace = "{expected_trace}"' in _WRAP_PROBE
    assert (
        '"/app/data/bin/velites", "sandbox", "wrap", "--cwd", "/tmp", "--", "/bin/true"'
        in _WRAP_PROBE
    )
    assert 'allow_network = [*no_network[:-4], "--allow-network", *no_network[-4:]]' in _WRAP_PROBE
    assert 'cases["explicit_userns_no_network"] = run_case(explicit)' in _WRAP_PROBE
    assert "if uid != 0:" in _WRAP_PROBE
    for key in ("CapEff", "CapPrm", "CapBnd", "NoNewPrivs"):
        assert key in _WRAP_PROBE
    for namespace in ("user", "pid", "net", "mnt"):
        assert namespace in _WRAP_PROBE
    assert "app_armor_current" in _WRAP_PROBE
    assert "result.stdout[-1024:]" in _WRAP_PROBE
    assert "result.stderr[-4096:]" in _WRAP_PROBE
    assert 'cases[f"{name}_dumpable_first"]' in _WRAP_PROBE
    assert (
        '[*command[:4], sys.executable, "-c", launcher, *command[4:-1], sys.executable, "-c", reset]'
        in _WRAP_PROBE
    )
    assert _WRAP_PROBE.count("sys.executable") == 2
    assert '"/app/data/bin/velites"' in _WRAP_PROBE
    assert "prctl(4,1,0,0,0)" in _WRAP_PROBE
    assert "raise SystemExit(subprocess.run(sys.argv[1:], check=False).returncode)" in _WRAP_PROBE
    assert "os.execvp" not in _WRAP_PROBE
    assert _WRAP_PROBE.count("os.execv(") == 1
    assert "prctl(4,0,0,0,0)" in _WRAP_PROBE
    assert "os.execv('/bin/true',['/bin/true'])" in _WRAP_PROBE
    assert _WRAP_PROBE.count("os.environ") == 2
    assert _WRAP_PROBE.count("os.environ.clear()") == 2
    for forbidden in (
        "docker inspect",
        "models.json",
        "control_token",
        "os.environ[",
        "os.environ.get",
        "os.getenv",
        "ptrace",
        "os.setuid",
        "os.setgid",
        "apt-get",
    ):
        assert forbidden not in _WRAP_PROBE


def test_bwrap_inner_preload_scopes_state_and_cleans_failures() -> None:
    from scripts.stress.worker_resource_bwrap_inner_evidence import (
        _C_SOURCE,
        _CLEANUP,
        _LOCK,
        _MARKER,
        _PRELOAD_GUARD,
        _SUPPORT,
        capture_inner_evidence,
    )

    for required in (
        "setns_before",
        "setns_after",
        "unshare_before",
        "unshare_after",
        "mount_before",
        "mount_after",
        "long syscall(long number, ...)",
        "SYS_mount",
        "SYS_setns",
        "SYS_unshare",
        "NS_GET_USERNS",
        "PR_GET_SECUREBITS",
        "mnt_owner_userns",
        "pid_owner_userns",
        "flags",
        "CapPrm",
        "CapEff",
        "CapBnd",
        "securebits",
        "NoNewPrivs",
        "Seccomp",
        "/proc/self/ns/user",
        "/proc/self/ns/mnt",
        "/proc/self/ns/pid",
        "/proc/self/attr/current",
    ):
        assert required in _C_SOURCE
    for forbidden in ("getenv", "models.json", "control_token", "SECRET"):
        assert forbidden not in _C_SOURCE
    assert _SUPPORT.startswith("set -e; ")
    assert "test ! -e /etc/ld.so.preload" in _SUPPORT
    assert "test ! -L /etc/ld.so.preload" in _PRELOAD_GUARD
    assert f"touch {_MARKER}" in _SUPPORT and _MARKER in _CLEANUP
    assert "/etc/ld.so.preload" in _CLEANUP and "/tmp/bwrap-inner.so" in _CLEANUP
    source = __import__("inspect").getsource(capture_inner_evidence)
    assert "with open(_LOCK" in source
    assert "fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)" in source
    assert source.index("with open(_LOCK") < source.index("rm -f {_MARKER}")
    assert _LOCK not in _CLEANUP

    calls = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        if "python3" in arguments:
            raise RuntimeError("probe failed")
        return ""

    run = SimpleNamespace(
        compose=compose,
        env={"MEASUREMENT_WORKER_UID": "501", "MEASUREMENT_WORKER_GID": "20"},
    )
    with pytest.raises(RuntimeError, match="probe failed"):
        capture_inner_evidence(run)
    assert calls[-1][-1].endswith(_CLEANUP)


def test_bwrap_inner_support_and_cleanup_failures_preserve_lifecycle() -> None:
    from scripts.stress.worker_resource_bwrap_inner_evidence import (
        _CLEANUP,
        _MARKER,
        _PRELOAD_GUARD,
        _SUPPORT,
        capture_inner_evidence,
    )

    calls = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        if arguments[-1] == _SUPPORT:
            raise RuntimeError("compile failed")
        if arguments[-1].endswith(_CLEANUP):
            raise RuntimeError("cleanup failed")
        return ""

    run = SimpleNamespace(
        compose=compose,
        env={"MEASUREMENT_WORKER_UID": "501", "MEASUREMENT_WORKER_GID": "20"},
    )
    with pytest.raises(RuntimeError) as caught:
        capture_inner_evidence(run)
    assert "compile failed" in str(caught.value)
    assert "cleanup failed" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "compile failed" in str(caught.value.__cause__)
    assert calls[0][-1] == f"rm -f {_MARKER}"
    assert calls[1][-1].startswith(_PRELOAD_GUARD)
    assert calls[-1][-1].endswith(_CLEANUP)


def test_bwrap_inner_guard_race_does_not_cleanup_user_file() -> None:
    from scripts.stress.worker_resource_bwrap_inner_evidence import (
        _CLEANUP,
        _MARKER,
        _SUPPORT,
        capture_inner_evidence,
    )

    calls = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        if arguments[-1] == _SUPPORT:
            raise RuntimeError("existing /etc/ld.so.preload")
        if arguments[-1] == _CLEANUP:
            raise AssertionError("cleanup must not run without the marker")
        return ""

    run = SimpleNamespace(
        compose=compose,
        env={"MEASUREMENT_WORKER_UID": "501", "MEASUREMENT_WORKER_GID": "20"},
    )
    with pytest.raises(RuntimeError, match="existing /etc/ld.so.preload"):
        capture_inner_evidence(run)
    assert calls[-1][-1].startswith(f"test ! -f {_MARKER}")


def test_bwrap_inner_lock_rejects_second_capture_until_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from threading import Event, Thread

    from scripts.stress import worker_resource_bwrap_inner_evidence as evidence

    lock = tmp_path / "inner.lock"
    started, release = Event(), Event()
    calls = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        if "python3" in arguments:
            started.set()
            assert release.wait(5)
            return '{"outer": {}, "cases": {}}'
        return ""

    run = SimpleNamespace(
        compose=compose,
        env={"MEASUREMENT_WORKER_UID": "501", "MEASUREMENT_WORKER_GID": "20"},
    )
    monkeypatch.setattr(evidence, "_LOCK", str(lock))
    result = {}
    thread = Thread(target=lambda: result.setdefault("first", evidence.capture_inner_evidence(run)))
    thread.start()
    assert started.wait(5)
    before = len(calls)
    with pytest.raises(RuntimeError, match="already running"):
        evidence.capture_inner_evidence(run)
    assert len(calls) == before
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert result["first"]["root"]["preload_events"] == []
    assert evidence.capture_inner_evidence(run)["root"]["preload_events"] == []


def test_wrap_evidence_installs_support_as_root_then_execs_requested_identity() -> None:
    from scripts.stress.worker_resource_bwrap_inner_evidence import (
        _CLEANUP,
        _EVENTS,
        _MARKER,
        _PRELOAD_GUARD,
        _SUPPORT,
    )
    from scripts.stress.worker_resource_parent_userns_evidence import _PARENT_WRITE_PROBE
    from scripts.stress.worker_resource_userns_evidence import _SELF_MAP_PROBE
    from scripts.stress.worker_resource_wrap_evidence import (
        _STRACE_SUPPORT,
        _WRAP_PROBE,
        _wrap_evidence,
    )

    calls = []
    trace = (
        "clone(child_stack=NULL, flags=SIGCHLD) = 123\n"
        "unshare(CLONE_NEWUSER) = 0\n"
        'openat(AT_FDCWD, "/proc/self/uid_map", O_RDWR) = 3\n'
        "write(3, mapping, 14) = 14\n"
    )

    def compose(*arguments: str, **kwargs: object) -> object:
        calls.append(arguments)
        assert kwargs == {"capture": True}
        return '{"outer": {}, "cases": {}}' if "python3" in arguments else ""

    def compose_result(*arguments: str) -> object:
        calls.append(arguments)
        if _PARENT_WRITE_PROBE in arguments:
            return SimpleNamespace(
                stdout='{"success": true, "after_maps": {"uid_map": "501 501 1\\n"}}',
                stderr="parent write uid_map = 10\n",
            )
        assert _SELF_MAP_PROBE in arguments
        return SimpleNamespace(stdout='{"success": true}', stderr=trace)

    run = SimpleNamespace(
        compose=compose,
        compose_result=compose_result,
        env={"MEASUREMENT_WORKER_UID": "501", "MEASUREMENT_WORKER_GID": "20"},
    )
    assert _wrap_evidence(run) == {
        "root": {"outer": {}, "cases": {}, "preload_events": []},
        "nonroot": {"outer": {}, "cases": {}, "preload_events": []},
        "nonroot_self_map": {
            "success": True,
            "trace": trace,
            "parent_write": {
                "success": True,
                "after_maps": {"uid_map": "501 501 1\n"},
                "trace": "parent write uid_map = 10\n",
            },
        },
    }
    assert "unshare(CLONE_NEWUSER)" in trace and "write(3, mapping, 14)" in trace
    assert calls[0] == ("exec", "-T", "--user", "0:0", "worker", "sh", "-c", _STRACE_SUPPORT)
    assert calls[1:-2] == [
        ("exec", "-T", "--user", "0:0", "worker", "sh", "-c", f"rm -f {_MARKER}"),
        (
            "exec",
            "-T",
            "--user",
            "0:0",
            "worker",
            "sh",
            "-c",
            f"{_PRELOAD_GUARD} || {{ echo 'existing /etc/ld.so.preload' >&2; exit 1; }}",
        ),
        ("exec", "-T", "--user", "0:0", "worker", "sh", "-c", _SUPPORT),
        ("exec", "-T", "--user", "0:0", "worker", "sh", "-c", f"rm -f {_EVENTS}"),
        (
            "exec",
            "-T",
            "--user",
            "0:0",
            "worker",
            "python3",
            "-c",
            _WRAP_PROBE,
            "0",
            "0",
        ),
        (
            "exec",
            "-T",
            "--user",
            "0:0",
            "worker",
            "sh",
            "-c",
            f"test ! -f {_EVENTS} || cat {_EVENTS}",
        ),
        ("exec", "-T", "--user", "0:0", "worker", "sh", "-c", f"rm -f {_EVENTS}"),
        (
            "exec",
            "-T",
            "--user",
            "501:20",
            "worker",
            "python3",
            "-c",
            _WRAP_PROBE,
            "501",
            "20",
        ),
        (
            "exec",
            "-T",
            "--user",
            "0:0",
            "worker",
            "sh",
            "-c",
            f"test ! -f {_EVENTS} || cat {_EVENTS}",
        ),
        ("exec", "-T", "--user", "0:0", "worker", "sh", "-c", f"test ! -f {_MARKER} || {_CLEANUP}"),
    ]
    assert calls[-2:] == [
        (
            "exec",
            "-T",
            "--user",
            "501:20",
            "worker",
            "strace",
            "-f",
            "-e",
            "trace=clone,clone3,fork,vfork,unshare,prctl,openat,write,setgroups",
            "python3",
            "-c",
            _SELF_MAP_PROBE,
            "501",
            "20",
        ),
        (
            "exec",
            "-T",
            "--user",
            "501:20",
            "worker",
            "strace",
            "-f",
            "-e",
            "trace=clone,clone3,fork,vfork,unshare,prctl,openat,write,setgroups,setns,fcntl,readlink,wait4,kill",
            "python3",
            "-c",
            _PARENT_WRITE_PROBE,
            "501",
            "20",
        ),
    ]


def test_wrap_evidence_preserves_support_install_failure() -> None:
    from scripts.stress.worker_resource_wrap_evidence import _STRACE_SUPPORT, _wrap_evidence

    calls = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        raise RuntimeError("support install failed")

    run = SimpleNamespace(
        compose=compose,
        env={"MEASUREMENT_WORKER_UID": "501", "MEASUREMENT_WORKER_GID": "20"},
    )
    result = _wrap_evidence(run)
    assert result == {"inner_error": "RuntimeError: support install failed"}
    assert calls == [("exec", "-T", "--user", "0:0", "worker", "sh", "-c", _STRACE_SUPPORT)]


def test_bounded_json_preserves_inner_events_and_long_string_tails() -> None:
    import json

    from scripts.stress.worker_resource_output import bounded_json

    trace = "trace-prefix" + "x" * 5000 + "trace-tail"
    message = "message-prefix" + "y" * 5000 + "message-tail"
    document = {
        "wrap": {
            "root": {
                "preload_events": [
                    {"event": "setns_before", "trace": trace},
                    {"event": "setns_after"},
                    {"event": "mount_before", "message": message},
                    {"event": "mount_after"},
                ]
            }
        }
    }
    text = bounded_json(document, 4096)
    assert len(text.encode("utf-8")) <= 4096
    events = json.loads(text)["wrap"]["root"]["preload_events"]
    assert [event["event"] for event in events] == [
        "setns_before",
        "setns_after",
        "mount_before",
        "mount_after",
    ]
    assert events[0]["trace"] == trace[-128:]
    assert events[2]["message"] == message[-128:]
