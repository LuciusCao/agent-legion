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
    assert '"inner_bwrap": inner' in _WRAP_PROBE
    assert "strace" in _WRAP_PROBE
    assert (
        '"/app/data/bin/velites", "sandbox", "wrap", "--cwd", "/tmp", "--", "/bin/true"'
        in _WRAP_PROBE
    )
    for key in ("CapEff", "CapPrm", "CapBnd", "NoNewPrivs"):
        assert key in _WRAP_PROBE
    for namespace in ("user", "pid", "net", "mnt"):
        assert namespace in _WRAP_PROBE
    assert "app_armor_current" in _WRAP_PROBE
    assert "result.stderr[-16384:]" in _WRAP_PROBE
    for forbidden in (
        "docker inspect",
        "models.json",
        "control_token",
        "os.environ",
        "ptrace",
        "os.setuid",
        "os.setgid",
        "apt-get",
    ):
        assert forbidden not in _WRAP_PROBE


def test_wrap_evidence_installs_support_as_root_then_execs_requested_identity() -> None:
    from scripts.stress.worker_resource_wrap_evidence import (
        _STRACE_SUPPORT,
        _WRAP_PROBE,
        _wrap_evidence,
    )

    calls = []

    def compose(*arguments: str, **kwargs: object) -> str:
        assert kwargs == {"capture": True}
        calls.append(arguments)
        return '{"outer": {}, "inner_bwrap": {}}' if "python3" in arguments else ""

    run = SimpleNamespace(
        compose=compose,
        env={"MEASUREMENT_WORKER_UID": "501", "MEASUREMENT_WORKER_GID": "20"},
    )
    assert _wrap_evidence(run) == {"outer": {}, "inner_bwrap": {}}
    assert calls == [
        ("exec", "-T", "--user", "0:0", "worker", "sh", "-c", _STRACE_SUPPORT),
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
