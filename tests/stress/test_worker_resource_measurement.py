from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.stress.worker_resource_cli import parse_args
from scripts.stress.worker_resource_measurement import (
    MEMORY_FILES,
    PIDS_FILES,
    CgroupSampler,
    CgroupV2Reader,
    discover_cgroup_v2,
    percentile_95,
    report_sample_counts,
    sanitize_log_text,
    summarize_samples,
)
from scripts.stress.worker_resource_workload import MeasurementRun, _definitions

pytestmark = pytest.mark.no_db


def test_all_measurement_workflows_pass_the_production_loader() -> None:
    from server.app.workflows.loader import workflow_definition_from_dict

    for code_concurrency in (1, 0):
        for workspace_id, definition in _definitions(code_concurrency).items():
            loaded = workflow_definition_from_dict(definition)
            assert loaded.key == workspace_id
            assert loaded.nodes["_start"].node_type == "start"
    assert set(_definitions(0)["resource_upload"]["nodes"]) == {"_start", "agent"}
    assert set(_definitions(1)["resource_upload"]["nodes"]) == {"_start", "resource_code"}


def _cgroup(root: Path, *, memory_max: str = "max", pids_max: str = "max") -> Path:
    root.mkdir(parents=True)
    (root / "memory.current").write_text("1024\n", encoding="utf-8")
    (root / "memory.peak").write_text("2048\n", encoding="utf-8")
    (root / "memory.max").write_text(f"{memory_max}\n", encoding="utf-8")
    (root / "memory.stat").write_text("anon 512\nfile 256\nkernel 128\n", encoding="utf-8")
    (root / "pids.current").write_text("7\n", encoding="utf-8")
    (root / "pids.max").write_text(f"{pids_max}\n", encoding="utf-8")
    (root / "pids.events").write_text("max 2\n", encoding="utf-8")
    return root


def _sample(kind: str, values: dict, scenario: str = "agent") -> dict:
    return {"kind": kind, "iteration": "cold-1", "scenario": scenario, "values": values}


def test_discover_cgroup_v2_uses_container_descendant_root(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    proc.joinpath("42").mkdir(parents=True)
    proc.joinpath("42/cgroup").write_text("0::/system.slice/docker-abc.scope\n", encoding="utf-8")
    root = _cgroup(tmp_path / "sys/fs/cgroup/system.slice/docker-abc.scope")
    assert discover_cgroup_v2(42, proc_root=proc, cgroup_root=tmp_path / "sys/fs/cgroup") == root


def test_discover_cgroup_v2_normalizes_only_docker_desktop_id_form(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    proc.joinpath("42").mkdir(parents=True)
    container_id = "a" * 64
    proc.joinpath("42/cgroup").write_text(f"0::/../{container_id}\n", encoding="utf-8")
    root = _cgroup(tmp_path / "sys/fs/cgroup/docker" / container_id)
    assert discover_cgroup_v2(42, proc_root=proc, cgroup_root=tmp_path / "sys/fs/cgroup") == root
    proc.joinpath("42/cgroup").write_text("0::/../..\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid or inaccessible"):
        discover_cgroup_v2(42, proc_root=proc, cgroup_root=tmp_path / "sys/fs/cgroup")


def test_cgroup_reader_fails_closed_and_reads_required_files(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="lacks required files"):
        CgroupV2Reader(tmp_path / "missing")
    reader = CgroupV2Reader(_cgroup(tmp_path / "cgroup", memory_max="4096", pids_max="16"))
    assert reader.memory_sample() == {
        "current_bytes": 1024,
        "peak_bytes": 2048,
        "max": "4096",
        "stat": {"anon": 512, "file": 256, "kernel": 128},
    }
    assert reader.pids_sample() == {"current": 7, "max": "16", "events": {"max": 2}}
    assert set(MEMORY_FILES + PIDS_FILES) == {
        "memory.current",
        "memory.peak",
        "memory.max",
        "memory.stat",
        "pids.current",
        "pids.max",
        "pids.events",
    }


def test_summary_has_nearest_rank_p95_peak_and_limit_headroom() -> None:
    memory = [
        _sample(
            "memory",
            {
                "current_bytes": value,
                "peak_bytes": value * 2,
                "max": "1000",
                "stat": {"file": value // 2},
            },
        )
        for value in (100, 200, 300, 400)
    ]
    pids = [
        _sample("pids", {"current": value, "max": "20", "events": {"max": 1}})
        for value in (3, 4, 5, 20)
    ]
    result = summarize_samples([*memory, *pids])["groups"]["cold-1/agent"]
    assert result["memory"]["current_p95_bytes"] == 400
    assert result["memory"]["current_max_bytes"] == 400
    assert result["memory"]["kernel_peak_max_bytes"] == 800
    assert result["memory"]["headroom_max_bytes"] == 600
    assert result["memory"]["headroom_kernel_peak_bytes"] == 200
    assert result["memory"]["stat_max"] == {"file": 200}
    assert result["pids"]["current_p95"] == 20
    assert result["pids"]["headroom_max"] == 0
    assert result["pids"]["events_max"] == {"max": 1}
    assert percentile_95(range(1, 101)) == 95


def test_summary_unlimited_headroom_is_null_not_a_default() -> None:
    result = summarize_samples(
        [
            _sample("memory", {"current_bytes": 100, "peak_bytes": 200, "max": "max", "stat": {}}),
            _sample("pids", {"current": 5, "max": "max", "events": {"max": 0}}),
        ]
    )
    group = result["groups"]["cold-1/agent"]
    assert group["memory"]["headroom_max_bytes"] is None
    assert group["pids"]["headroom_max"] is None
    assert result["recommended_limits"] is None


def test_outer_sample_counts_match_all_all_not_idle_subset() -> None:
    summary = {
        "groups": {
            "all/all": {"sample_counts": {"memory": 28, "pids": 132}},
            "cold-1/idle-registered": {"sample_counts": {"memory": 0, "pids": 2}},
        }
    }
    assert report_sample_counts(summary) == {"memory": 28, "pids": 132}
    assert report_sample_counts({"groups": {}}) == {"memory": 0, "pids": 0}


def test_sampler_retains_raw_samples_and_scenario_markers(tmp_path: Path) -> None:
    sampler = CgroupSampler(
        CgroupV2Reader(_cgroup(tmp_path / "cgroup")), tmp_path / "samples.jsonl"
    )
    sampler.mark("cold-1", "register")
    sampler.start()
    sampler.mark("cold-1", "agent")
    sampler.sample_memory()
    sampler.sample_pids()
    sampler.record_backlog(active=1, queued=2)
    assert sampler.stop() == []
    rows = [
        json.loads(line)
        for line in (tmp_path / "samples.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {row["kind"] for row in rows} == {"marker", "memory", "pids", "backlog"}
    assert any(
        row["kind"] == "backlog" and row["values"]["upload_queued_count"] == 2 for row in rows
    )
    assert any(row["scenario"] == "register" for row in rows)
    assert any(row["kind"] == "memory" and row["values"]["stat"]["file"] == 256 for row in rows)
    summary_path = tmp_path / "summary.json"
    sampler.write_summary(summary_path)
    assert json.loads(summary_path.read_text(encoding="utf-8"))["sampling_errors"] == []


def test_sampler_recovers_transient_reader_errors_but_keeps_persistent_errors(
    tmp_path: Path,
) -> None:
    class Reader:
        def __init__(self) -> None:
            import threading

            self.memory_calls = 0
            self.recovered = threading.Event()

        def memory_sample(self) -> dict:
            self.memory_calls += 1
            if self.memory_calls == 1:
                raise OSError("cgroup replaced")
            self.recovered.set()
            return {"current_bytes": 1, "peak_bytes": 1, "max": "max", "stat": {}}

        def pids_sample(self) -> dict:
            raise OSError("persistent reader failure")

    reader = Reader()
    sampler = CgroupSampler(reader, tmp_path / "samples.jsonl")
    sampler.start()
    assert reader.recovered.wait(2)
    errors = sampler.stop()
    assert not any(error.startswith("memory:") for error in errors)
    assert any(error.startswith("pids:") for error in errors)


def test_stop_final_sample_can_clear_transient_reader_errors(tmp_path: Path) -> None:
    class Reader:
        def __init__(self) -> None:
            self.memory_calls = 0
            self.pids_calls = 0

        def memory_sample(self) -> dict:
            self.memory_calls += 1
            if self.memory_calls == 1:
                raise OSError("restart")
            return {"current_bytes": 1, "peak_bytes": 1, "max": "max", "stat": {}}

        def pids_sample(self) -> dict:
            self.pids_calls += 1
            if self.pids_calls == 1:
                raise OSError("restart")
            return {"current": 1, "max": "max", "events": {"max": 0}}

    sampler = CgroupSampler(Reader(), tmp_path / "samples.jsonl")
    sampler.start()
    assert sampler.stop() == []


def test_log_sanitizer_removes_credentials_and_presigned_query() -> None:
    text = 'Authorization: Bearer control-secret {"register_token":"register-secret"} ?X-Amz-Signature=abc&x=1'
    result = sanitize_log_text(text, ["control-secret", "register-secret"])
    assert "control-secret" not in result
    assert "register-secret" not in result
    assert "X-Amz-Signature=abc" not in result
    assert "x=1" in result


def test_seed_initializes_schema_before_first_connection_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    from scripts.stress import worker_resource_workload as workload

    events: list[str] = []

    class JobDB:
        def list_workspaces(self) -> list[dict]:
            return [{"id": workspace} for workspace in workload.WORKSPACES.values()]

    monkeypatch.setattr(
        workload,
        "JobQueries",
        lambda *_args, **_kwargs: events.append("schema") or JobDB(),
    )

    @contextmanager
    def transaction(_dsn: str):
        events.append("insert")
        yield SimpleNamespace(execute=lambda *_args: None)

    monkeypatch.setattr(workload, "write_transaction", transaction)
    monkeypatch.setattr(workload, "_ensure_skill_repo", lambda _path: None)
    monkeypatch.setattr(workload, "NodeCodeService", lambda _dsn: object())
    monkeypatch.setattr(workload, "seed_workspace_node_code", lambda *_args: None)
    monkeypatch.setattr(
        workload,
        "WorkflowRevisionService",
        lambda _db: SimpleNamespace(ensure_active_revision=lambda *_args: None),
    )
    workload.seed_measurement_workspaces("dsn", Path("skills"), 3)
    assert events[:2] == ["schema", "insert"]


def test_measurement_worker_identity_and_models_home_match_host(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    worker = yaml.safe_load((root / "deploy/compose.worker.measurement.yaml").read_text())[
        "services"
    ]["worker"]
    assert worker["build"]["args"] == {
        "WORKER_UID": "${MEASUREMENT_WORKER_UID:?generated by worker_resource_workload.py}",
        "WORKER_GID": "${MEASUREMENT_WORKER_GID:?generated by worker_resource_workload.py}",
    }
    assert worker["user"] == (
        "${MEASUREMENT_WORKER_UID:?generated by worker_resource_workload.py}:"
        "${MEASUREMENT_WORKER_GID:?generated by worker_resource_workload.py}"
    )
    assert worker["environment"]["HOME"] == "/home/agent-worker"
    assert any(
        isinstance(mount, str) and mount.endswith(":/home/agent-worker/.velites/models.json:ro")
        for mount in worker["volumes"]
    )
    assert not any(
        isinstance(mount, str) and "/root/.velites" in mount for mount in worker["volumes"]
    )

    args = parse_args(
        ["--output-dir", str(tmp_path / "results"), "--work-dir", str(tmp_path / "work")]
    )
    run = MeasurementRun(args)
    assert run.env["MEASUREMENT_WORKER_UID"] == str(os.getuid())
    assert run.env["MEASUREMENT_WORKER_GID"] == str(os.getgid())


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
        "CapPrm",
        "CapEff",
        "CapBnd",
        "Securebits",
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
    assert "test ! -h /etc/ld.so.preload" in _PRELOAD_GUARD
    assert f"touch {_MARKER}" in _SUPPORT and _MARKER in _CLEANUP
    assert "/etc/ld.so.preload" in _CLEANUP and "/tmp/bwrap-inner.so" in _CLEANUP

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
    assert calls == [
        ("exec", "-T", "--user", "0:0", "worker", "sh", "-c", _STRACE_SUPPORT),
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


def test_self_map_probe_is_minimal_and_stops_matrix_on_failure() -> None:
    from scripts.stress.worker_resource_parent_userns_evidence import _PARENT_WRITE_PROBE
    from scripts.stress.worker_resource_userns_evidence import _SELF_MAP_PROBE
    from scripts.stress.worker_resource_wrap_evidence import _STRACE_SUPPORT, _wrap_evidence

    for required in (
        "before_unshare",
        "after_unshare",
        "before_map_write",
        "write_uid_map",
        "CapPrm",
        "CapEff",
        "CapBnd",
        "NoNewPrivs",
        "Seccomp",
        "dumpable",
        "app_armor_current",
        "parent_pid",
        "real_uid",
        "effective_uid",
        "uid_map",
    ):
        assert required in _SELF_MAP_PROBE
    assert "os.environ.clear()" in _SELF_MAP_PROBE
    for forbidden in ("os.environ[", "os.environ.get", "os.getenv", "models.json", "control_token"):
        assert forbidden not in _SELF_MAP_PROBE

    calls = []

    def compose(*arguments: str, **_kwargs: object) -> object:
        calls.append(arguments)
        if _STRACE_SUPPORT in arguments:
            return ""
        raise AssertionError("self-map failure must stop the full matrix")

    def compose_result(*arguments: str) -> object:
        calls.append(arguments)
        if _PARENT_WRITE_PROBE in arguments:
            return SimpleNamespace(
                stdout='{"success": true, "after_maps": {"uid_map": "501 501 1\\n"}}',
                stderr="parent write uid_map = 10\n",
            )
        assert _SELF_MAP_PROBE in arguments
        return SimpleNamespace(
            stdout='{"success": false, "failure": "write EPERM"}',
            stderr=(
                "unshare(CLONE_NEWUSER) = 0\n"
                'openat(AT_FDCWD, "/proc/self/uid_map", O_RDWR) = 3\n'
                "write(3, mapping, 14) = -1 EPERM (Operation not permitted)\n"
            ),
        )

    run = SimpleNamespace(
        compose=compose,
        compose_result=compose_result,
        env={"MEASUREMENT_WORKER_UID": "501", "MEASUREMENT_WORKER_GID": "20"},
    )
    result = _wrap_evidence(run)
    assert result["nonroot_self_map"]["success"] is False
    assert "write(3, mapping, 14) = -1 EPERM" in result["nonroot_self_map"]["trace"]
    assert result["nonroot_self_map"]["parent_write"]["success"] is True
    assert len(calls) == 3 and calls[0][-1] == _STRACE_SUPPORT


def test_parent_write_probe_uses_bidirectional_sync_and_fail_closed() -> None:
    from scripts.stress.worker_resource_parent_userns_evidence import _PARENT_WRITE_PROBE

    for required in (
        "select.select([go_read], [], [], 5)",
        "select.select([ready_read], [], [], 5)",
        'write_once("uid_map", f"/proc/{child}/uid_map"',
        'write_once("setgroups", f"/proc/{child}/setgroups", "deny\\n")',
        'write_once("gid_map", f"/proc/{child}/gid_map"',
        'libc.open(f"/proc/{child}/ns/user".encode(), os.O_RDONLY)',
        "libc.fcntl(ns_fd, 1)",
        "libc.setns(ns_fd, 0x10000000)",
        "select.select([read], [], [], 5)",
        "for _ in range(500)",
        'else: raise TimeoutError("validator exit")',
        'report["cleanup_status"] = cleanup(pid)',
        'parent_report["target_cleanup_status"] = cleanup(child)',
        'parent_report["target_reaped"] = True',
        "os.close(ns_fd)",
        "os.kill(pid, 9)",
        "os.waitpid(child, 0)",
        "os.environ.clear()",
    ):
        assert required in _PARENT_WRITE_PROBE
    assert _PARENT_WRITE_PROBE.index('write_once("setgroups"') < _PARENT_WRITE_PROBE.index(
        'write_once("gid_map"'
    )
    assert _PARENT_WRITE_PROBE.index("os.write(ready_write") < _PARENT_WRITE_PROBE.index(
        "select.select([go_read]"
    )
    assert (
        _PARENT_WRITE_PROBE.index('write_once("gid_map"')
        < _PARENT_WRITE_PROBE.index('libc.open(f"/proc/{child}/ns/user"')
        < _PARENT_WRITE_PROBE.index("os.write(go_write")
    )
    assert _PARENT_WRITE_PROBE.index("os.waitpid(child, 0)") < _PARENT_WRITE_PROBE.index(
        'parent_report["setns"] = validate(ns_fd)'
    )
    for forbidden in ("os.environ[", "os.environ.get", "os.getenv", "models.json", "control_token"):
        assert forbidden not in _PARENT_WRITE_PROBE


def test_parent_write_validator_success_requires_lifecycle_and_namespace() -> None:
    from scripts.stress.worker_resource_parent_userns_evidence import _PARENT_WRITE_PROBE

    for required in (
        'validation.get("result") == 0',
        "os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0",
        'validation.get("reaped") is True',
        'validation.get("namespace") == parent_report["parent_open"]["namespace"]',
        'not parent_report["parent_open"]["flags"] & 1',
        'parent_report.get("failure") or validation.get("failure", "validator validation failed")',
    ):
        assert required in _PARENT_WRITE_PROBE


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
    with pytest.raises(RuntimeError, match="support install failed"):
        _wrap_evidence(run)
    assert calls == [("exec", "-T", "--user", "0:0", "worker", "sh", "-c", _STRACE_SUPPORT)]


def test_installer_apparmor_override_is_explicitly_test_only() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github/workflows/worker-resource-measurement.yml").read_text()
    )
    assert workflow["jobs"]["installer-lifecycle"]["env"]["MEASUREMENT_APPARMOR"] == (
        "apparmor:unconfined"
    )
    for name in ("compose.worker.yaml", "compose.worker.standalone.yaml", "compose.host.yaml"):
        worker = yaml.safe_load((root / "deploy" / name).read_text())["services"]["worker"]
        assert worker["security_opt"] == ["seccomp:unconfined"]
