from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.stress.worker_resource_evidence import BacklogEvidence, measure_upload_backlog
from scripts.stress.worker_resource_execution import require_exclusive_execution_routes
from scripts.stress.worker_resource_host_code import (
    HOST_CODE_SANDBOX_OVERRIDE,
    host_code_sandbox_metadata,
)
from scripts.stress.worker_resource_identity import (
    ContractViolationError,
    ExecutionReadinessError,
    build_execution_expectations,
    require_direct_outputs,
    require_done_executions,
    verify_authority_object,
    verify_remote_uploads,
)
from scripts.stress.worker_resource_local_code import (
    host_code_direct_refs,
    require_host_code_leases,
)
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
from scripts.stress.worker_resource_workload import SCENARIOS, _definitions, parse_args

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]


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


def test_measurement_compose_preserves_shipped_envelope_and_optional_limits() -> None:
    document = yaml.safe_load(
        (ROOT / "deploy/compose.worker.measurement.yaml").read_text(encoding="utf-8")
    )
    worker = document["services"]["worker"]
    assert worker["cap_add"] == ["SYS_ADMIN", "${MEASUREMENT_NET_ADMIN:-SYS_ADMIN}"]
    assert worker["security_opt"] == [
        "seccomp:unconfined",
        "${MEASUREMENT_APPARMOR:-apparmor:unconfined}",
    ]
    assert worker["mem_limit"] == "${MEASUREMENT_MEM_LIMIT:-0}"
    assert worker["pids_limit"] == "${MEASUREMENT_PIDS_LIMIT:--1}"
    assert "env_file" not in worker and "user" not in worker
    host = document["services"]["host"]
    assert "cap_add" not in host and "security_opt" not in host
    startup = host["command"][0]
    assert 'exec setpriv --reuid="${MEASUREMENT_HOST_UID}"' in startup
    assert '--regid="${MEASUREMENT_HOST_GID}" --clear-groups' in startup
    assert "env HOME=/tmp uvicorn" in startup
    assert host["volumes"][1].endswith(":/tmp/.agents/skills:ro")
    assert "safe.directory" not in startup
    production = yaml.safe_load((ROOT / "deploy/compose.host.yaml").read_text(encoding="utf-8"))
    assert production["services"]["worker"]["cap_add"] == ["SYS_ADMIN"]
    assert production["services"]["worker"]["security_opt"] == ["seccomp:unconfined"]
    assert all(value not in yaml.safe_dump(production) for value in ("apparmor:", "SYS_PTRACE"))
    assert "--no-sandbox" not in yaml.safe_dump(document)


def test_default_host_code_sandbox_is_disposable_and_probed() -> None:
    document = yaml.safe_load(HOST_CODE_SANDBOX_OVERRIDE)
    assert set(document["services"]) == {"host"}
    host = document["services"]["host"]
    assert host["cap_add"] == ["SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE"]
    assert host["security_opt"] == ["seccomp:unconfined", "apparmor:unconfined"]
    assert "bubblewrap" in host["command"][0] and "test -u /usr/bin/bwrap" in host["command"][0]
    run = SimpleNamespace(compose=lambda *_args, **_kwargs: "bubblewrap 0.10.0\n4755")
    metadata = host_code_sandbox_metadata(run)
    assert metadata["path"] == "/usr/bin/bwrap" and metadata["setuid"] is True
    assert metadata["security_opt"] == host["security_opt"]
    assert metadata["support"] == "non-root setuid bwrap REQUIRED_CAPS_0"
    assert metadata["production_recommendation"] is False


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
    evidence_source = (ROOT / "scripts/stress/worker_resource_identity.py").read_text(
        encoding="utf-8"
    )
    assert "agent_execution_requests" in evidence_source
    args = parse_args(["--cold-repetitions", "1", "--warm-repetitions", "1"])
    assert args.pids_interval_ms == 100
    assert args.artifact_chars == 64 * 1024
    with pytest.raises(SystemExit):
        parse_args(["--jobs-per-wave", "1"])
    assert parse_args(["--jobs-per-wave", "2"]).jobs_per_wave == 2
    limited = parse_args(["--code-concurrency", "0", "--mem-limit", "200m", "--pids-limit", "48"])
    assert (limited.code_concurrency, limited.mem_limit, limited.pids_limit) == (0, "200m", 48)
    for invalid in (["--code-concurrency", "-1"], ["--pids-limit", "0"], ["--mem-limit", " "]):
        with pytest.raises(SystemExit):
            parse_args(invalid)


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
        workspace_id="resource_code",
        job_id="job1",
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
            storage,
            execution_id="exec1",
            workspace_id="resource_code",
            job_id="job1",
            name="code_output.json",
            ref=_direct_ref(),
            row=row,
        )


def test_gzip_authority_object_checks_compressed_size_and_plaintext_hash() -> None:
    compressed = gzip.compress(b"abc")
    ref = {**_direct_ref(), "storage_key": STAGING_KEY + ".gz", "size_bytes": len(compressed)}
    row = {**_authority_row(), "storage_key": AUTHORITY_KEY + ".gz", "size_bytes": len(compressed)}
    evidence = verify_authority_object(
        _Storage(data=compressed),
        execution_id="exec1",
        workspace_id="resource_code",
        job_id="job1",
        name="code_output.json",
        ref=ref,
        row=row,
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

    def raise_for_status(self) -> None:
        return


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


def _identity_nodes() -> dict:
    return {
        "first": {"type": "code", "outputs": ["a.json", "b.json"]},
        "second": {"type": "agent", "outputs": ["c.json"]},
    }


def _identity_expectations() -> dict:
    jobs = ["job1", "job2"]
    nodes = _identity_nodes()
    return build_execution_expectations(
        job_ids=jobs,
        workspace_id="resource_mixed",
        nodes=nodes,
        job_rows=[{"id": job, "workspace_id": "resource_mixed"} for job in jobs],
        job_node_rows=[{"job_id": job, "node_key": node} for job in jobs for node in nodes],
        node_run_rows=[
            {"id": 1, "job_id": "job1", "node_key": "first"},
            {"id": 11, "job_id": "job1", "node_key": "first"},
            {"id": 12, "job_id": "job1", "node_key": "second"},
            {"id": 21, "job_id": "job2", "node_key": "first"},
            {"id": 22, "job_id": "job2", "node_key": "second"},
        ],
    )


def _execution_row(job: str, node: str, run: int, execution: str) -> dict:
    return {
        "execution_id": execution,
        "workspace_id": "resource_mixed",
        "job_id": job,
        "node_key": node,
        "node_run_id": run,
        "kind": "agent" if node == "second" else "code",
        "worker_id": "resource-measurement-worker",
        "state": "done",
        "outcome_json": {"output_artifacts": {}},
    }


def test_duplicate_rows_cannot_replace_another_jobs_missing_execution() -> None:
    expected = _identity_expectations()
    rows = [_execution_row("job1", "first", 11, "e11"), _execution_row("job1", "second", 12, "e12")]
    with pytest.raises(ExecutionReadinessError, match="job2"):
        require_done_executions(expected, rows, worker_id="resource-measurement-worker")


def test_duplicate_current_execution_identity_fails() -> None:
    expected = _identity_expectations()
    rows = [
        _execution_row("job1", "first", 11, "e11"),
        _execution_row("job1", "first", 11, "e11-duplicate"),
    ]
    with pytest.raises(ContractViolationError, match="duplicate current execution"):
        require_done_executions(expected, rows, worker_id="resource-measurement-worker")


def test_legal_multi_node_and_multi_output_identities_pass() -> None:
    expected = _identity_expectations()
    rows = [
        _execution_row("job1", "first", 11, "e11"),
        _execution_row("job1", "second", 12, "e12"),
        _execution_row("job2", "first", 21, "e21"),
        _execution_row("job2", "second", 22, "e22"),
    ]
    assert (
        len(require_done_executions(expected, rows, worker_id="resource-measurement-worker")) == 4
    )
    execution = {
        "execution_id": "e11",
        "job_id": "job1",
        "outcome_json": {
            "output_artifacts": {
                "a.json": {
                    **_direct_ref(),
                    "storage_key": "jobs-staging/resource_mixed/job1/e11/a.json",
                },
                "b.json": {
                    **_direct_ref(),
                    "storage_key": "jobs-staging/resource_mixed/job1/e11/b.json",
                },
            }
        },
    }
    assert len(require_direct_outputs(execution, ["a.json", "b.json"], "resource_mixed")) == 2


def test_default_code_zero_uses_current_host_leases_and_authority_refs() -> None:
    expected = {
        key: value for key, value in _identity_expectations().items() if value["kind"] == "code"
    }
    leases = [
        {
            "execution_id": execution_id,
            "executor_id": "code",
            "workspace_id": "resource_mixed",
            "job_id": job_id,
            "node_key": "first",
            "node_run_id": run_id,
            "status": "released",
        }
        for job_id, run_id, execution_id in (("job1", 11, "e11"), ("job2", 21, "e21"))
    ]
    done = require_host_code_leases(expected, leases)
    assert len(done) == 2
    with pytest.raises(ContractViolationError, match="one current Host code lease"):
        require_host_code_leases(expected, [leases[0], leases[0]])
    leases[1]["status"] = "active"
    with pytest.raises(ContractViolationError, match="terminal state"):
        require_host_code_leases(expected, leases)
    execution = done[("job1", "first", 11)]
    authority = {
        ("job1", "first", name): {
            "storage_key": f"jobs/resource_mixed/job1/.v/attempt/{name}.gz",
            "size_bytes": 3,
            "content_hash": DIGEST,
        }
        for name in ("a.json", "b.json")
    }
    refs = host_code_direct_refs(execution, ["a.json", "b.json"], "resource_mixed", authority)
    assert refs["a.json"]["storage_key"].endswith("/e11/a.json.gz")
    assert refs["b.json"]["size_bytes"] == 3


def _lease_row(job: str, node: str, run: int, execution: str, executor_id: str = "code") -> dict:
    return {
        "execution_id": execution,
        "executor_id": executor_id,
        "workspace_id": "resource_mixed",
        "job_id": job,
        "node_key": node,
        "node_run_id": run,
        "status": "released",
    }


def _exclusive_inputs() -> dict:
    expected = _identity_expectations()
    host_expected = {key: value for key, value in expected.items() if value["kind"] == "code"}
    remote_expected = {key: value for key, value in expected.items() if key not in host_expected}
    execution_rows = [
        _execution_row("job1", "second", 12, "e12"),
        _execution_row("job2", "second", 22, "e22"),
    ]
    lease_rows = [
        _lease_row("job1", "first", 11, "e11"),
        _lease_row("job2", "first", 21, "e21"),
    ]
    return {
        "remote_expected": remote_expected,
        "host_expected": host_expected,
        "execution_rows": execution_rows,
        "lease_rows": lease_rows,
        "remote_done": require_done_executions(
            remote_expected, execution_rows, worker_id="resource-measurement-worker"
        ),
        "host_done": require_host_code_leases(host_expected, lease_rows),
    }


def test_current_execution_routes_are_unique_and_history_cannot_fill_or_conflict() -> None:
    inputs = _exclusive_inputs()
    inputs["execution_rows"].append(_execution_row("job1", "first", 1, "historical-worker"))
    inputs["lease_rows"].append(_lease_row("job1", "second", 2, "historical-host"))
    require_exclusive_execution_routes(**inputs)


def test_remote_code_and_agent_lane_leases_are_not_host_code_conflicts() -> None:
    inputs = _exclusive_inputs()
    inputs["lease_rows"].append(_lease_row("job1", "second", 12, "agent-lane", "agent:worker"))
    require_exclusive_execution_routes(**inputs)
    code_key = ("job1", "first", 11)
    code_row = _execution_row("job1", "first", 11, "remote-code")
    inputs["execution_rows"].append(code_row)
    inputs["remote_done"][code_key] = code_row
    inputs["remote_expected"][code_key] = inputs["host_expected"][code_key]
    inputs["host_expected"] = {}
    inputs["host_done"] = {}
    inputs["lease_rows"] = [row for row in inputs["lease_rows"] if row["node_key"] == "second"]
    inputs["lease_rows"].append(
        _lease_row("job1", "first", 11, "remote-code-lease", "agent:code:worker")
    )
    require_exclusive_execution_routes(**inputs)


def test_host_lease_matching_only_selects_local_code() -> None:
    expected = {("job1", "first", 11): _identity_expectations()[("job1", "first", 11)]}
    leases = [
        _lease_row("job1", "first", 11, "local"),
        _lease_row("job1", "first", 11, "remote", "agent:code:worker"),
        _lease_row("job1", "first", 11, "agent", "agent:worker"),
    ]
    assert len(require_host_code_leases(expected, leases)) == 1


def test_host_lease_and_done_worker_execution_on_same_current_key_fails() -> None:
    inputs = _exclusive_inputs()
    inputs["execution_rows"].append(_execution_row("job1", "first", 11, "worker-code"))
    with pytest.raises(ContractViolationError, match="conflicting Worker execution"):
        require_exclusive_execution_routes(**inputs)


@pytest.mark.parametrize("status", ["queued", "claimed", "failed"])
def test_any_worker_status_on_host_expected_current_key_fails(status: str) -> None:
    inputs = _exclusive_inputs()
    conflict = _execution_row("job1", "first", 11, "worker-code")
    conflict["state"] = status
    inputs["execution_rows"].append(conflict)
    with pytest.raises(ContractViolationError, match="conflicting Worker execution"):
        require_exclusive_execution_routes(**inputs)


def test_host_lease_on_worker_expected_current_key_fails() -> None:
    inputs = _exclusive_inputs()
    inputs["lease_rows"].append(_lease_row("job1", "second", 12, "host-agent"))
    with pytest.raises(ContractViolationError, match="conflicting Host lease"):
        require_exclusive_execution_routes(**inputs)


def test_worker_and_host_execution_ids_must_be_globally_disjoint() -> None:
    inputs = _exclusive_inputs()
    inputs["host_done"][("job1", "first", 11)]["execution_id"] = "e12"
    with pytest.raises(ContractViolationError, match="not disjoint"):
        require_exclusive_execution_routes(**inputs)


def test_verify_remote_uploads_rejects_conflicting_worker_row_on_host_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    execution_rows = _exclusive_inputs()["execution_rows"]
    execution_rows.append(_execution_row("job1", "first", 11, "worker-code"))
    lease_rows = _exclusive_inputs()["lease_rows"]

    class Conn:
        def execute(self, sql: str, _params: object) -> object:
            rows = {
                " from jobs ": [
                    {"id": "job1", "workspace_id": "resource_mixed"},
                    {"id": "job2", "workspace_id": "resource_mixed"},
                ],
                " from job_nodes ": [
                    {"job_id": job, "node_key": node}
                    for job in ("job1", "job2")
                    for node in ("first", "second")
                ],
                " from node_runs ": [
                    {"id": 11, "job_id": "job1", "node_key": "first"},
                    {"id": 12, "job_id": "job1", "node_key": "second"},
                    {"id": 21, "job_id": "job2", "node_key": "first"},
                    {"id": 22, "job_id": "job2", "node_key": "second"},
                ],
                " from agent_execution_requests": execution_rows,
                " from executor_leases where job_id = any(%s) and executor_id = %s": lease_rows,
            }
            for fragment, result in rows.items():
                if fragment in sql:
                    return SimpleNamespace(fetchall=lambda result=result: result)
            raise AssertionError(f"unexpected SQL: {sql}")

    @contextmanager
    def fake_connection(_dsn: str):
        yield Conn()

    monkeypatch.setattr("scripts.stress.worker_resource_identity.read_connection", fake_connection)
    with pytest.raises(ContractViolationError, match="conflicting Worker execution"):
        verify_remote_uploads(
            dsn="dsn",
            storage=_Storage(),
            workspace_id="resource_mixed",
            nodes=_identity_nodes(),
            job_ids=["job1", "job2"],
            worker_id="resource-measurement-worker",
            allow_host_code=True,
        )


@pytest.mark.parametrize(
    "key",
    [
        "jobs/other/job1/.v/attempt/code_output.json",
        "jobs/resource_code/other/.v/attempt/code_output.json",
        "jobs/resource_code/job1/.v/attempt/other.json",
    ],
)
def test_cross_workspace_job_or_output_authority_key_fails(key: str) -> None:
    with pytest.raises(ContractViolationError, match="authority key is not for"):
        verify_authority_object(
            _Storage(),
            execution_id="exec1",
            workspace_id="resource_code",
            job_id="job1",
            name="code_output.json",
            ref=_direct_ref(),
            row={**_authority_row(), "storage_key": key},
        )


def test_authority_form_mismatch_fails_and_legacy_layout_passes() -> None:
    with pytest.raises(ContractViolationError, match="gzip form mismatch"):
        verify_authority_object(
            _Storage(),
            execution_id="exec1",
            workspace_id="resource_code",
            job_id="job1",
            name="code_output.json",
            ref={**_direct_ref(), "storage_key": STAGING_KEY + ".gz"},
            row=_authority_row(),
        )
    evidence = verify_authority_object(
        _Storage(),
        execution_id="exec1",
        workspace_id="resource_code",
        job_id="job1",
        name="code_output.json",
        ref=_direct_ref(),
        row={**_authority_row(), "storage_key": "jobs/resource_code/job1/code_output.json"},
    )
    assert evidence["object_verified"] is True


def _completed_session() -> object:
    response = _FakeResponse({"job_stats": {"total": 1, "by_status": {"completed": 1}}})
    return SimpleNamespace(get=lambda *_args, **_kwargs: response)


def _ready_run() -> object:
    from scripts.stress.worker_resource_workload import MeasurementRun

    run = MeasurementRun.__new__(MeasurementRun)
    run.args = SimpleNamespace(jobs_per_wave=1, job_timeout_seconds=2.0)
    run.host_url = "http://host"
    return run


def test_completed_before_done_waits_until_execution_outcome_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.stress.worker_resource_workload.time.sleep", lambda _: None)
    run = _ready_run()
    calls = 0

    def verify(_job_ids: list[str], _scenario: str) -> dict:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ExecutionReadinessError("not visible")
        return {"direct_upload_verified": True}

    monkeypatch.setattr(run, "_verify_remote", verify)
    result = run._await_run(
        _completed_session(),
        "code",
        {"workspace_id": "resource_code", "run_id": "run1", "job_ids": ["job1"]},
    )
    assert result["direct_upload_verified"] is True
    assert calls == 3


def test_contract_failure_is_not_retried_after_job_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.stress.worker_resource_workload.time.sleep", lambda _: None)
    run = _ready_run()
    calls = 0

    def verify(_job_ids: list[str], _scenario: str) -> dict:
        nonlocal calls
        calls += 1
        raise ContractViolationError("CAS fallback")

    monkeypatch.setattr(run, "_verify_remote", verify)
    with pytest.raises(ContractViolationError, match="CAS fallback"):
        run._await_run(
            _completed_session(),
            "code",
            {"workspace_id": "resource_code", "run_id": "run1", "job_ids": ["job1"]},
        )
    assert calls == 1
