from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.stress.worker_resource_evidence import (
    BacklogEvidence,
    measure_upload_backlog,
    require_direct_outputs,
    verify_authority_object,
)
from scripts.stress.worker_resource_measurement import (
    MEMORY_FILES,
    PIDS_FILES,
    CgroupSampler,
    CgroupV2Reader,
    discover_cgroup_v2,
    percentile_95,
    sanitize_log_text,
    summarize_samples,
)
from scripts.stress.worker_resource_workload import SCENARIOS, _definitions, parse_args

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]


def test_all_measurement_workflows_pass_the_production_loader() -> None:
    from server.app.workflows.loader import workflow_definition_from_dict

    for workspace_id, definition in _definitions().items():
        loaded = workflow_definition_from_dict(definition)
        assert loaded.key == workspace_id
        assert loaded.nodes["_start"].node_type == "start"


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


def test_log_sanitizer_removes_credentials_and_presigned_query() -> None:
    text = 'Authorization: Bearer control-secret {"register_token":"register-secret"} ?X-Amz-Signature=abc&x=1'
    result = sanitize_log_text(text, ["control-secret", "register-secret"])
    assert "control-secret" not in result
    assert "register-secret" not in result
    assert "X-Amz-Signature=abc" not in result
    assert "x=1" in result


def test_measurement_compose_preserves_shipped_envelope_without_new_limits() -> None:
    document = yaml.safe_load(
        (ROOT / "deploy/compose.worker.measurement.yaml").read_text(encoding="utf-8")
    )
    worker = document["services"]["worker"]
    assert worker["cap_add"] == ["SYS_ADMIN"]
    assert worker["security_opt"] == ["seccomp:unconfined"]
    assert "mem_limit" not in worker
    assert "pids_limit" not in worker
    assert "env_file" not in worker
    assert "user" not in worker


def test_workload_covers_real_execution_and_restart_scenarios() -> None:
    assert set(SCENARIOS) == {
        "agent",
        "code",
        "mixed",
        "upload-backlog",
        "pending-restart",
        "executor-restart",
        "container-restart",
    }
    source = (ROOT / "scripts/stress/worker_resource_workload.py").read_text(encoding="utf-8")
    assert "simulate_agents" not in source
    assert "agent_execution_requests" in source
    args = parse_args(["--cold-repetitions", "1", "--warm-repetitions", "1"])
    assert args.pids_interval_ms == 100
    assert args.artifact_chars == 64 * 1024


def test_empty_or_deduplicated_wave_cannot_pass_as_complete() -> None:
    from scripts.stress.worker_resource_workload import MeasurementRun

    run = MeasurementRun.__new__(MeasurementRun)
    run.args = SimpleNamespace(jobs_per_wave=2)
    with pytest.raises(RuntimeError, match="expected measurement wave"):
        run._await_run(object(), "agent", {"job_ids": [], "workspace_id": "ws", "run_id": "run"})


def test_wait_retries_compose_readiness_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from scripts.stress.worker_resource_workload import MeasurementRun

    monkeypatch.setattr("scripts.stress.worker_resource_workload.time.sleep", lambda _: None)
    calls = 0

    def probe() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.CalledProcessError(1, ["pg_isready"])
        return "ready"

    run = MeasurementRun.__new__(MeasurementRun)
    assert run._wait("PostgreSQL", probe) == "ready"
    assert calls == 2


def test_workflow_is_dispatch_only_native_linux_and_uploads_only_results() -> None:
    workflow_path = ROOT / ".github/workflows/worker-resource-measurement.yml"
    source = workflow_path.read_text(encoding="utf-8")
    document = yaml.safe_load(source)
    assert set(document[True]) == {"workflow_dispatch"}
    assert document["permissions"] == {"contents": "read"}
    assert "ubuntu-24.04" in source
    assert "worker-resource-results/" in source
    assert "data/worker-resource-measurement/work" not in source
    assert "secrets." not in source
    assert "workflow_run" not in source


DIGEST = hashlib.sha256(b"abc").hexdigest()
STAGING_KEY = "jobs-staging/resource_code/job1/exec1/code_output.json"
AUTHORITY_KEY = "jobs/resource_code/job1/.v/attempt/code_output.json"


def _direct_ref() -> dict:
    return {"storage_key": STAGING_KEY, "size_bytes": 3, "content_hash": DIGEST}


def _execution(ref: object) -> dict:
    return {
        "execution_id": "exec1",
        "job_id": "job1",
        "outcome_json": json.dumps({"output_artifacts": {"code_output.json": ref}}),
    }


def _authority_row() -> dict:
    return {
        "storage_key": AUTHORITY_KEY,
        "size_bytes": 3,
        "content_hash": DIGEST,
    }


class _Storage:
    def __init__(self, data: bytes = b"abc", *, head: bool = True, head_size: int | None = None):
        self.data = data
        self.head = head
        self.head_size = len(data) if head_size is None else head_size

    def head_object(self, _key: str) -> object:
        return SimpleNamespace(size_bytes=self.head_size) if self.head else None

    def open_stream(self, _key: str) -> io.BytesIO:
        return io.BytesIO(self.data)


def test_direct_output_and_authority_object_evidence_passes() -> None:
    refs = require_direct_outputs(_execution(_direct_ref()), ["code_output.json"], "resource_code")
    evidence = verify_authority_object(
        _Storage(),
        execution_id="exec1",
        name="code_output.json",
        ref=refs["code_output.json"],
        row=_authority_row(),
    )
    assert evidence["object_verified"] is True
    assert evidence["direct_ref"]["storage_key"] == STAGING_KEY
    assert evidence["authority_key"] == AUTHORITY_KEY


def test_cas_fallback_and_missing_output_refs_fail() -> None:
    with pytest.raises(RuntimeError, match="legacy CAS"):
        require_direct_outputs(
            _execution("sha256:cas-fallback"), ["code_output.json"], "resource_code"
        )
    with pytest.raises(RuntimeError, match="output set mismatch"):
        require_direct_outputs(
            {"execution_id": "exec1", "outcome_json": {}},
            ["code_output.json"],
            "resource_code",
        )


@pytest.mark.parametrize("field", ["storage_key", "size_bytes", "content_hash"])
def test_incomplete_or_wrong_direct_ref_fails(field: str) -> None:
    ref = _direct_ref()
    ref[field] = {"storage_key": "jobs/other", "size_bytes": -1, "content_hash": "bad"}[field]
    with pytest.raises(RuntimeError, match="wrong staging key|invalid size|invalid hash"):
        require_direct_outputs(_execution(ref), ["code_output.json"], "resource_code")


@pytest.mark.parametrize(
    "mutation", ["row_missing", "row_size", "row_hash", "head_missing", "head_size", "object_hash"]
)
def test_authority_or_object_mismatch_fails(mutation: str) -> None:
    row = None if mutation == "row_missing" else _authority_row()
    storage = _Storage()
    if mutation == "row_size":
        row["size_bytes"] += 1
    elif mutation == "row_hash":
        row["content_hash"] = "0" * 64
    elif mutation == "head_missing":
        storage = _Storage(head=False)
    elif mutation == "head_size":
        storage = _Storage(head_size=4)
    elif mutation == "object_hash":
        storage = _Storage(data=b"abd")
    with pytest.raises(RuntimeError, match="job_artifacts|missing|mismatch"):
        verify_authority_object(
            storage, execution_id="exec1", name="code_output.json", ref=_direct_ref(), row=row
        )


def test_gzip_authority_object_checks_compressed_size_and_plaintext_hash() -> None:
    compressed = gzip.compress(b"abc")
    ref = {**_direct_ref(), "storage_key": STAGING_KEY + ".gz", "size_bytes": len(compressed)}
    row = {**_authority_row(), "storage_key": AUTHORITY_KEY + ".gz", "size_bytes": len(compressed)}
    evidence = verify_authority_object(
        _Storage(data=compressed), execution_id="exec1", name="code_output.json", ref=ref, row=row
    )
    assert evidence["object_verified"] is True


def test_zero_backlog_never_passes() -> None:
    evidence = BacklogEvidence()
    evidence.observe(
        {"upload_active_count": 1, "upload_queued_count": 0},
        recorded_at="t1",
        monotonic_seconds=1.0,
    )
    evidence.observe(
        {"upload_active_count": 0, "upload_queued_count": 3},
        recorded_at="t2",
        monotonic_seconds=2.0,
    )
    with pytest.raises(RuntimeError, match="backlog was not observed"):
        evidence.summary()


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def json(self) -> dict:
        return self.payload


class _FakeBacklogRun:
    def __init__(self, *, backlog: bool, fail_pause: bool = False):
        self.args = SimpleNamespace(upload_concurrency=3, backlog_timeout_seconds=1.0)
        self.backlog = backlog
        self.fail_pause = fail_pause
        self.events: list[object] = []

    def compose(self, *args: str) -> None:
        self.events.append(args)
        if args == ("pause", "seaweedfs") and self.fail_pause:
            raise RuntimeError("pause failed")

    def _worker_request(self, method: str, path: str, _token: str, **kwargs: object) -> object:
        self.events.append((method, path, kwargs.get("json")))
        if path == "/api/status":
            return _FakeResponse(
                {"upload_active_count": 1, "upload_queued_count": 1 if self.backlog else 0}
            )
        return _FakeResponse({})

    def _create_run(self, _session: object, _scenario: str, _iteration: str) -> dict:
        self.events.append("create")
        return {"run_id": "run1"}

    def _wait(self, _description: str, probe: object, *, timeout: float) -> None:
        assert timeout == 1.0
        probe()
        if not self.backlog:
            raise RuntimeError("timed out")

    def _await_run(self, _session: object, _scenario: str, pending: dict) -> dict:
        self.events.append(("await", pending["run_id"]))
        return {"direct_upload_verified": True}


class _FakeSampler:
    def __init__(self) -> None:
        self.observations: list[tuple[int, int]] = []

    def record_backlog(self, *, active: int, queued: int) -> None:
        self.observations.append((active, queued))


def test_backlog_barrier_sequences_pause_observe_unpause_restore_then_verify() -> None:
    run = _FakeBacklogRun(backlog=True)
    sampler = _FakeSampler()
    result = measure_upload_backlog(run, object(), "cold-1", "control", sampler)
    assert result["observation_count"] == 1
    assert result["peak_active"] == 1
    assert result["peak_queued"] == 1
    assert result["barrier_released"] is True
    assert result["direct_upload_verified"] is True
    assert sampler.observations == [(1, 1)]
    assert run.events.index(("pause", "seaweedfs")) < run.events.index("create")
    assert run.events.index("create") < run.events.index(("unpause", "seaweedfs"))
    assert run.events.index(("unpause", "seaweedfs")) < run.events.index(("await", "run1"))
    assert ("PUT", "/api/config", {"upload_max_concurrency": 3}) in run.events


def test_zero_backlog_still_unpauses_and_restores_without_completion() -> None:
    run = _FakeBacklogRun(backlog=False)
    with pytest.raises(RuntimeError, match="backlog was not observed"):
        measure_upload_backlog(run, object(), "cold-1", "control", _FakeSampler())
    assert ("unpause", "seaweedfs") in run.events
    assert ("PUT", "/api/config", {"upload_max_concurrency": 3}) in run.events
    assert not any(isinstance(event, tuple) and event[0] == "await" for event in run.events)


def test_pause_error_still_attempts_unpause_and_restores_concurrency() -> None:
    run = _FakeBacklogRun(backlog=False, fail_pause=True)
    with pytest.raises(RuntimeError, match="pause failed"):
        measure_upload_backlog(run, object(), "cold-1", "control", _FakeSampler())
    assert ("unpause", "seaweedfs") in run.events
    assert ("PUT", "/api/config", {"upload_max_concurrency": 3}) in run.events
    assert "create" not in run.events
