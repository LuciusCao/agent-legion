from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.stress.worker_resource_workload import MeasurementRun
from server.app.jobs.storage_layout import job_shard


def _code_row(absolute: bool = False, iteration: str = "cold-1") -> dict:
    job = f"resource~ucode_resource~ucode_measurement-ref:measurement-{iteration}-code-0-secret"
    shard = job_shard(job)
    relative = f"logs/jobs/by-job/{shard}/{job}/resource_code.log"
    run_dir = f"jobs/resource_code/{shard}/{job}/runs/resource_code/token"
    return {
        "id": 1,
        "job_id": job,
        "node_key": "resource_code",
        "workspace_id": "resource_code",
        "status": "failed",
        "error_message": "no result secret",
        "command_json": ["--password", "unknown-secret"],
        "log_path": f"/var/lib/agent-legion/{relative}" if absolute else relative,
        "run_dir": f"/var/lib/agent-legion/{run_dir}" if absolute else run_dir,
    }


def _diagnostic_run(log: str = "sandbox stderr secret") -> object:
    calls = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        return "service log" if arguments[:2] == ("logs", "--no-color") else log

    return SimpleNamespace(
        args=SimpleNamespace(code_concurrency=0),
        dsn="dsn",
        secrets=["secret"],
        control_token="",
        cgroup_reader_id="",
        env={},
        compose=compose,
        calls=calls,
    )


def _diagnostic_connection(rows: list[dict]):
    from contextlib import contextmanager

    @contextmanager
    def connection(_dsn: str):
        current = [row for row in rows if "measurement-cold-1" in row["job_id"]]
        yield SimpleNamespace(execute=lambda *_args: SimpleNamespace(fetchall=lambda: current))

    return connection


@pytest.mark.no_db
def test_production_relative_absolute_and_iteration_scoped_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress.worker_resource_reporting import write_sanitized_logs

    rows = [_code_row(), _code_row(absolute=True), _code_row(iteration="cold-0")]
    monkeypatch.setattr(
        "scripts.stress.worker_resource_diagnostics.read_connection", _diagnostic_connection(rows)
    )
    monkeypatch.setattr(
        "scripts.stress.worker_resource_reporting.write_host_code_runtime_evidence_safely",
        lambda _run, path: (path / "host-code-runtime.json").write_text("{}", encoding="utf-8"),
    )
    run = _diagnostic_run()
    write_sanitized_logs(run, tmp_path)
    assert (tmp_path / "host-code-runtime.json").is_file()
    text = (tmp_path / "host-code.log").read_text()
    assert "sandbox stderr" in text and "secret" not in text
    metadata = (tmp_path / "host-code-runs.json").read_text()
    assert (
        "secret" not in metadata and "unknown-secret" not in metadata and "cold-0" not in metadata
    )
    assert "O_NOFOLLOW" in str(run.calls) and "O_DIRECTORY" in str(run.calls)
    run.args.code_concurrency = 1
    other = tmp_path / "worker-code"
    other.mkdir()
    write_sanitized_logs(run, other)
    assert not (other / "host-code.log").exists()
    assert not (other / "host-code-runtime.json").exists()


@pytest.mark.no_db
def test_log_path_rejects_parent_other_job_and_run_dir_mismatch() -> None:
    from scripts.stress.worker_resource_diagnostics import _log_path

    row = _code_row()
    shard = job_shard(row["job_id"])
    wrong_shard = "00" if shard != "00" else "01"
    for absent in (None, ""):
        assert _log_path({**row, "run_dir": absent}) == row["log_path"]
    for path in (
        "../escape.log",
        f"logs/jobs/by-job/{shard}/other/resource_code.log",
        f"logs/jobs/by-job/{wrong_shard}/{row['job_id']}/resource_code.log",
        "/etc/passwd",
    ):
        with pytest.raises(ValueError):
            _log_path({**row, "log_path": path})
    for run_dir in (
        "jobs/other/runs/resource_code/token",
        f"jobs/not-a-workspace/{shard}/{row['job_id']}/not-runs/resource_code/token",
        f"jobs/not-a-workspace/{shard}/{row['job_id']}/runs/resource_code/token",
        f"jobs/resource_code/{wrong_shard}/{row['job_id']}/runs/resource_code/token",
        f"jobs/resource_code/{shard}/other/runs/resource_code/token",
        f"jobs/resource_code/{shard}/{row['job_id']}/runs/resource_code",
        f"jobs/resource_code/{shard}/{row['job_id']}/runs/resource_code/..",
    ):
        with pytest.raises(ValueError, match="run_dir ownership"):
            _log_path({**row, "run_dir": run_dir})


@pytest.mark.no_db
def test_read_log_rejects_intermediate_and_leaf_symlinks(tmp_path: Path) -> None:
    import subprocess

    from scripts.stress.worker_resource_diagnostics import _READ_LOG

    root = tmp_path / "root"
    job = "resource~ucode_resource~ucode_measurement-ref:measurement-cold-1-code-0"
    outside = tmp_path / "outside" / job
    outside.mkdir(parents=True)
    (outside / "resource_code.log").write_text("OUTSIDE-REGULAR-FILE", encoding="utf-8")
    shard = job_shard(job)
    relative = f"logs/jobs/by-job/{shard}/{job}/resource_code.log"
    for leaf in (False, True):
        link = root / relative if leaf else root / "logs/jobs/by-job" / shard
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(
            outside / "resource_code.log" if leaf else outside.parent, target_is_directory=not leaf
        )
        result = subprocess.run(
            ["python3", "-c", _READ_LOG, relative, str(root)], capture_output=True
        )
        assert result.returncode != 0
        link.unlink()
    (root / relative).write_text("safe")
    result = subprocess.run(["python3", "-c", _READ_LOG, relative, str(root)], capture_output=True)
    assert result.returncode == 0 and result.stdout == b"safe"


@pytest.mark.no_db
def test_operational_error_preserves_original_failure_cleanup_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    run = MeasurementRun.__new__(MeasurementRun)
    run.args = SimpleNamespace(code_concurrency=0)
    run.output_dir = tmp_path / "cold-1"
    run.secrets = ["secret"]
    run.env = {}
    run.dsn = "dsn"
    run.control_token = ""
    run.cgroup_reader_id = "reader-1"
    downs = []

    def compose(*arguments: str, **_kwargs: object) -> str:
        if arguments[0] == "up":
            raise RuntimeError("original workload failure")
        downs.append(arguments) if arguments[0] == "down" else None
        return ""

    run.compose = compose
    monkeypatch.setattr(
        "scripts.stress.worker_resource_diagnostics.read_connection",
        lambda *_args: (_ for _ in ()).throw(psycopg.OperationalError("db down secret")),
    )
    removals = []
    monkeypatch.setattr(
        "scripts.stress.worker_resource_workload.subprocess.run",
        lambda *args, **_kwargs: removals.append(args) or SimpleNamespace(stdout="", stderr=""),
    )
    result = run.run_iteration("cold", 1)
    assert result["error"] == "RuntimeError: original workload failure"
    error_file = tmp_path / "cold-1/cold-1/host-code-diagnostics-error.txt"
    assert error_file.is_file()
    assert "OperationalError" in error_file.read_text() and "secret" not in error_file.read_text()
    assert (tmp_path / "cold-1/cold-1/host-code-runtime-error.txt").is_file()
    assert len(downs) == 2 and (tmp_path / "cold-1/cold-1/result.json").is_file()
    assert run.cgroup_reader_id == "" and any("reader-1" in str(call) for call in removals)


@pytest.mark.no_db
def test_artifact_byte_budgets_and_unknown_argv_never_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress.worker_resource_reporting import write_sanitized_logs

    rows = [{**_code_row(), "id": index, "error_message": "界" * 100000} for index in range(20)]
    monkeypatch.setattr(
        "scripts.stress.worker_resource_diagnostics.read_connection", _diagnostic_connection(rows)
    )
    run = _diagnostic_run(log="x" * 65536)
    write_sanitized_logs(run, tmp_path)
    assert (tmp_path / "host-code.log").stat().st_size <= 1 << 20
    metadata_path = tmp_path / "host-code-runs.json"
    assert metadata_path.stat().st_size <= 256 * 1024 + 2
    assert "unknown-secret" not in metadata_path.read_text()
    assert isinstance(json.loads(metadata_path.read_text()), list)


@pytest.mark.postgres
def test_host_code_diagnostics_uses_real_join_iteration_and_node_scope(tmp_path: Path) -> None:
    from scripts.stress.worker_resource_diagnostics import write_host_code_diagnostics
    from server.app.db.transaction import write_transaction
    from tests.postgres_support import TEST_DATABASE_URL

    jobs = [
        f"resource~ucode_resource~ucode_measurement-ref:measurement-{iteration}-code-{index}"
        for iteration, index in (("cold-1", 0), ("cold-1", 1), ("cold-0", 0))
    ]
    agent_job = "resource~uagent_resource~uagent_measurement-ref:measurement-cold-1-agent-0"
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("insert into workspaces(id, name) values ('resource_code', 'Resource code')")
        conn.execute("insert into workspaces(id, name) values ('resource_agent', 'Resource agent')")
        for job in jobs:
            conn.execute(
                "insert into jobs(id, workspace_id, source_type, source_id) values (%s, %s, %s, %s)",
                (job, "resource_code", "measurement", job),
            )
            conn.execute(
                "insert into node_runs(job_id, node_key, status, error_message, log_path, run_dir)"
                " values (%s, %s, %s, %s, %s, %s)",
                (
                    job,
                    "resource_code",
                    "failed",
                    "no result secret",
                    f"logs/jobs/by-job/{job_shard(job)}/{job}/resource_code.log",
                    ""
                    if job == jobs[0]
                    else f"jobs/resource_code/{job_shard(job)}/{job}/runs/resource_code/token",
                ),
            )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id) values (%s, %s, %s, %s)",
            (agent_job, "resource_agent", "measurement", agent_job),
        )
        conn.execute(
            "insert into node_runs(job_id, node_key, status, error_message, log_path, run_dir)"
            " values (%s, %s, %s, %s, %s, %s)",
            (
                agent_job,
                "resource_agent",
                "failed",
                "agent failure secret",
                f"logs/jobs/by-job/{job_shard(agent_job)}/{agent_job}/resource_agent.log",
                f"jobs/resource_agent/{job_shard(agent_job)}/{agent_job}"
                "/runs/resource_agent/worker",
            ),
        )

    iteration_dir = tmp_path / "cold-1"
    iteration_dir.mkdir()
    run = _diagnostic_run(log="postgres log secret")
    run.dsn = TEST_DATABASE_URL
    write_host_code_diagnostics(run, iteration_dir)

    metadata = json.loads((iteration_dir / "host-code-runs.json").read_text(encoding="utf-8"))
    assert [item["job_id"] for item in metadata] == jobs[:2]
    assert metadata[0]["run_dir"] == ""
    assert all("cold-0" not in item["job_id"] for item in metadata)
    assert all(item["error_message"] == "no result <redacted>" for item in metadata)
    log = (iteration_dir / "host-code.log").read_text(encoding="utf-8")
    assert log.count("postgres log") == 2 and "secret" not in log and agent_job not in log


@pytest.mark.no_db
def test_runtime_probe_reports_real_process_and_bwrap_shape(tmp_path: Path) -> None:
    import subprocess

    from scripts.stress.worker_resource_runtime_evidence import _RUNTIME_PROBE

    proc = tmp_path / "proc"
    (proc / "self/attr").mkdir(parents=True)
    (proc / "self/status").write_text(
        "Uid:\t1001\t0\t0\t1001\nGid:\t1001\t1001\t1001\t1001\n"
        "Securebits:\t00\nCapPrm:\t000001ff\nCapInh:\t00000000\nCapAmb:\t00000000\n"
        "CapEff:\t00000100\nCapBnd:\t000001ff\nNoNewPrivs:\t0\n"
        "Seccomp:\t0\nSeccomp_filters:\t0\n",
        encoding="utf-8",
    )
    (proc / "self/attr/current").write_text("docker-default (enforce)\n", encoding="utf-8")
    (proc / "self/mountinfo").write_text(
        "1 0 0:1 / / rw,nosuid - ext4 /dev/root rw\n"
        "2 1 0:2 /tmp /tmp rw,relatime - tmpfs tmpfs rw\n",
        encoding="utf-8",
    )
    bwrap = tmp_path / "bwrap"
    bwrap.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "bubblewrap 0.10.0"; exit 0; fi\n'
        'echo "capset failed" >&2; exit 1\n',
        encoding="utf-8",
    )
    bwrap.chmod(0o4755)
    result = subprocess.run(
        ["python3", "-c", _RUNTIME_PROBE, str(proc), str(bwrap)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["status"] == {
        "Uid": "1001\t0\t0\t1001",
        "Gid": "1001\t1001\t1001\t1001",
        "Securebits": "00",
        "CapPrm": "000001ff",
        "CapInh": "00000000",
        "CapAmb": "00000000",
        "CapEff": "00000100",
        "CapBnd": "000001ff",
        "NoNewPrivs": "0",
        "Seccomp": "0",
        "Seccomp_filters": "0",
    }
    assert evidence["app_armor_current"] == "docker-default (enforce)"
    assert evidence["identity"]["uid"] == bwrap.stat().st_uid == evidence["identity"]["uid"]
    assert evidence["bwrap"]["realpath"] == str(bwrap)
    assert evidence["bwrap"]["mode"] == "4755" and evidence["bwrap"]["setuid"] is True
    assert evidence["bwrap"]["mount"] == {"mount_point": "/", "nosuid": True}
    assert evidence["bwrap"]["tmp_mount"] == {"mount_point": "/tmp", "nosuid": False}
    assert evidence["bwrap"]["version"]["stdout"] == "bubblewrap 0.10.0\n"
    assert evidence["bwrap"]["smoke"] == {
        "returncode": 1,
        "stdout": "",
        "stderr": "capset failed\n",
    }


@pytest.mark.no_db
def test_runtime_evidence_uses_actual_compose_identity_and_allowlisted_inspect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.stress import worker_resource_runtime_evidence as evidence

    calls = []
    config = {
        "services": {
            service: {
                "cap_add": ["SYS_ADMIN"],
                "security_opt": ["seccomp:unconfined"],
                "environment": {"PASSWORD": "secret"},
            }
            for service in ("host", "worker")
        }
    }

    def compose(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        if arguments[:3] == ("config", "--format", "json"):
            return json.dumps(config)
        if arguments[:2] == ("ps", "-q"):
            return f"{arguments[2]}-id"
        return json.dumps({"app_armor_current": "docker-default secret"})

    inspect_calls = []

    def inspect(command: list[str], **_kwargs: object) -> object:
        inspect_calls.append(command)
        assert command[3] == "{{json .HostConfig.CapAdd}}|{{json .HostConfig.SecurityOpt}}"
        return SimpleNamespace(stdout='["SYS_ADMIN"]|["seccomp:unconfined"]')

    monkeypatch.setattr(evidence.subprocess, "run", inspect)
    run = SimpleNamespace(
        compose=compose,
        env={"MEASUREMENT_HOST_UID": "123", "MEASUREMENT_HOST_GID": "456"},
        secrets=["secret"],
    )
    evidence.write_host_code_runtime_evidence(run, tmp_path)
    text = (tmp_path / "host-code-runtime.json").read_text(encoding="utf-8")
    document = json.loads(text)
    assert "secret" not in text and "environment" not in text
    assert set(document["compose"]["host"]) == {"cap_add", "security_opt", "privileged", "user"}
    assert document["containers"]["host"]["process"]["app_armor_current"] == (
        "docker-default <redacted>"
    )
    assert [call[-1] for call in inspect_calls] == ["host-id", "worker-id"]
    assert "setuid_strace" in document["containers"]["host"]
    assert "setuid_strace" not in document["containers"]["worker"]
    exec_calls = [call for call in calls if call[0] == "exec"]
    assert exec_calls[0][3:7] == ("setpriv", "--reuid=123", "--regid=456", "--clear-groups")
    worker_exec = next(call for call in exec_calls if call[2] == "worker")
    assert worker_exec[3] == "python3" and "setpriv" not in worker_exec


@pytest.mark.no_db
def test_setuid_strace_probe_traces_transition_and_preserves_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess

    from scripts.stress.worker_resource_setuid_evidence import _SETUID_STRACE_PROBE

    strace = tmp_path / "strace"
    strace.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n"
        "echo 'setuid(123) = 0' >&2\n"
        "echo 'capset(...) = -1 EPERM (Operation not permitted)' >&2\n"
        "echo 'ptrace(PTRACE_TRACEME) = -1 EPERM (Operation not permitted)' >&2\n"
        "exit 7\n",
        encoding="utf-8",
    )
    strace.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        ["python3", "-c", _SETUID_STRACE_PROBE, "123", "456", "/usr/bin/bwrap"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["returncode"] == 7
    assert "setuid(123) = 0" in evidence["stderr"]
    assert "capset(...) = -1 EPERM" in evidence["stderr"]
    assert "ptrace(PTRACE_TRACEME) = -1 EPERM" in evidence["stderr"]
    arguments = evidence["stdout"].splitlines()
    transition = arguments[arguments.index("setpriv") : arguments.index("setpriv") + 4]
    assert transition == ["setpriv", "--reuid=123", "--regid=456", "--clear-groups"]
    assert arguments[-4:] == [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--die-with-parent",
        "/bin/true",
    ]
