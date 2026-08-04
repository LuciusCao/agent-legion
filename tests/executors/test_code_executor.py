from __future__ import annotations

import shutil
import subprocess
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from server.app.executors.code import CodeExecutor
from server.app.executors.config import CodeCapabilityConfig
from server.app.executors.models import ExecutionContext

REPO_ROOT = Path(__file__).resolve().parents[2]
VELITES_DEBUG_BINARY = REPO_ROOT / "velites" / "target" / "debug" / "velites"


def _velites_binary() -> Path:
    """Prebuilt debug binary, or a cargo build (skipped when cargo is absent)."""
    if VELITES_DEBUG_BINARY.exists():
        return VELITES_DEBUG_BINARY
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("no prebuilt velites binary and cargo is not available")
    proc = subprocess.run(
        [cargo, "build", "--manifest-path", str(REPO_ROOT / "velites" / "Cargo.toml")],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not VELITES_DEBUG_BINARY.exists():
        pytest.skip(f"velites build failed: {proc.stderr[-400:]}")
    return VELITES_DEBUG_BINARY


def _sandboxed(monkeypatch: pytest.MonkeyPatch) -> None:
    binary = _velites_binary()
    monkeypatch.setattr(
        "server.app.executors._code_sandbox.shutil.which", lambda _name: str(binary)
    )


@pytest.fixture
def context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="code-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="question_comprehension_info",
        node_key="fetch_questions",
        capability="fetch_questions",
        workspace={"id": "ws-a"},
        job={
            "id": "job-1",
            "workspace_id": "ws-a",
            "workflow_key": "question_comprehension_info",
            "source_type": "question",
            "source_id": "q-1",
            "batch_id": "",
            "title": "Question 1",
            "storage_dir": str(tmp_path),
            "stem": "",
        },
        job_dir=tmp_path,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=("out.json",),
    )


def _write_node(repo_root: Path, name: str, body: str) -> str:
    path = repo_root / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return name


def _executor(
    repo_root: Path,
    path: str,
    *,
    timeout_seconds: int = 60,
) -> CodeExecutor:
    return CodeExecutor(
        "code-default",
        {"fetch_questions": CodeCapabilityConfig(path=path, timeout_seconds=timeout_seconds)},
        repo_root=repo_root,
    )


def test_constructor_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside the repository root"):
        _executor(tmp_path, "nodes/missing.py")


def test_constructor_rejects_non_file_path(tmp_path: Path) -> None:
    (tmp_path / "nodes").mkdir()
    with pytest.raises(ValueError, match="inside the repository root"):
        _executor(tmp_path, "nodes")


def test_config_rejects_absolute_and_escape_paths() -> None:
    with pytest.raises(ValueError, match="must not be absolute"):
        CodeCapabilityConfig(path="/etc/passwd")
    with pytest.raises(ValueError, match="must not contain '..'"):
        CodeCapabilityConfig(path="../outside.py")


def test_supports(tmp_path: Path) -> None:
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    assert executor.supports("fetch_questions")
    assert not executor.supports("other")


def test_execute_missing_capability(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(replace(context, capability="missing"))
    assert result.status == "failed"
    assert "not supported" in result.error_message


def test_execute_success_writes_expected_outputs(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(
        tmp_path,
        "node_ok.py",
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text("{}", encoding="utf-8")
        """,
    )
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "completed"
    assert result.produced_artifacts == ("out.json",)


def test_execute_fails_when_outputs_missing(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_noop.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "Missing outputs" in result.error_message


def test_execute_propagates_node_exception(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(
        tmp_path,
        "node_boom.py",
        """
        def run(job, job_dir, runtime):
            raise RuntimeError("boom")
        """,
    )
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "RuntimeError: boom" in result.error_message


def test_execute_fails_without_run_callable(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_no_run.py", "VALUE = 1\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "callable 'run'" in result.error_message


def test_execute_timeout_kills_child(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(
        tmp_path,
        "node_slow.py",
        """
        import time

        def run(job, job_dir, runtime):
            time.sleep(60)
        """,
    )
    executor = _executor(tmp_path, path, timeout_seconds=1)
    result = executor.execute(context)
    assert result.status == "failed"
    assert "timed out after 1s" in result.error_message


def test_cancel_before_start(tmp_path: Path, context: ExecutionContext) -> None:
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    executor.cancel(context.execution_id)
    result = executor.execute(context)
    assert result.status == "cancelled"


def test_execute_custom_node_code_from_source(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """context.node_code (EXEC-CODE-002) runs sandboxed from the string, not the file."""
    _sandboxed(monkeypatch)
    path = _write_node(
        tmp_path,
        "node_builtin.py",
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{"origin": "builtin"}', encoding="utf-8")
        """,
    )
    custom_source = textwrap.dedent(
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{"origin": "custom"}', encoding="utf-8")
        """
    )
    executor = _executor(tmp_path, path)
    result = executor.execute(replace(context, node_code=custom_source))
    assert result.status == "completed"
    assert (tmp_path / "out.json").read_text(encoding="utf-8") == '{"origin": "custom"}'


def test_execute_custom_node_code_without_run_fails(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sandboxed(monkeypatch)
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)
    result = executor.execute(replace(context, node_code="X = 1\n"))
    assert result.status == "failed"
    assert "callable 'run'" in result.error_message


def test_custom_node_code_fails_closed_without_sandbox(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No velites wrapper -> custom code never runs unsandboxed (EXEC-CODE-003)."""
    monkeypatch.setattr("server.app.executors._code_sandbox.shutil.which", lambda _name: None)
    custom_source = textwrap.dedent(
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{}', encoding="utf-8")
        """
    )
    path = _write_node(
        tmp_path,
        "node_builtin.py",
        """
        def run(job, job_dir, runtime):
            (job_dir / "out.json").write_text('{"origin": "builtin"}', encoding="utf-8")
        """,
    )
    executor = _executor(tmp_path, path)

    custom = executor.execute(replace(context, node_code=custom_source))
    assert custom.status == "failed"
    assert "refusing to run unsandboxed" in custom.error_message
    assert not (tmp_path / "out.json").exists()

    # Builtin nodes are unaffected: they keep the bare multiprocessing child.
    builtin = executor.execute(replace(context, node_code=None, execution_id="exec-builtin"))
    assert builtin.status == "completed"
    assert (tmp_path / "out.json").read_text(encoding="utf-8") == '{"origin": "builtin"}'


def test_custom_sandbox_denies_writes_outside_job_dir(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS seatbelt integration: writes outside job_dir/tmp fail with EPERM."""
    _sandboxed(monkeypatch)
    custom_source = textwrap.dedent(
        """
        from pathlib import Path

        def run(job, job_dir, runtime):
            (Path.home() / ".agent-legion-sandbox-probe").write_text("x", encoding="utf-8")
        """
    )
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)

    result = executor.execute(replace(context, node_code=custom_source))

    assert result.status == "failed"
    assert "Operation not permitted" in result.error_message
    assert not (Path.home() / ".agent-legion-sandbox-probe").exists()


def test_custom_sandbox_denies_reads_outside_allowlist(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reads of files outside the allowlist (e.g. $HOME) fail with EPERM."""
    _sandboxed(monkeypatch)
    custom_source = textwrap.dedent(
        """
        from pathlib import Path

        def run(job, job_dir, runtime):
            Path(Path.home() / ".ssh" / "id_rsa").read_text(encoding="utf-8")
        """
    )
    probe = Path.home() / ".ssh" / "id_rsa"
    if not probe.exists():
        probe.parent.mkdir(exist_ok=True)
        probe.write_text("probe", encoding="utf-8")
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")
    executor = _executor(tmp_path, path)

    result = executor.execute(replace(context, node_code=custom_source))

    assert result.status == "failed"
    assert "Operation not permitted" in result.error_message


def test_custom_sandbox_denies_network_by_default(
    tmp_path: Path, context: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outbound network is denied unless the capability opts in (EXEC-CODE-003)."""
    _sandboxed(monkeypatch)
    custom_source = textwrap.dedent(
        """
        import urllib.request

        def run(job, job_dir, runtime):
            urllib.request.urlopen("http://127.0.0.1:9/", timeout=2)
        """
    )
    path = _write_node(tmp_path, "node_ok.py", "def run(job, job_dir, runtime):\n    pass\n")

    denied = _executor(tmp_path, path).execute(replace(context, node_code=custom_source))
    assert denied.status == "failed"
    assert "Operation not permitted" in denied.error_message

    executor_with_net = CodeExecutor(
        "code-default",
        {
            "fetch_questions": CodeCapabilityConfig(
                path=path, timeout_seconds=60, sandbox_network=True
            )
        },
        repo_root=tmp_path,
    )
    allowed = executor_with_net.execute(
        replace(context, node_code=custom_source, execution_id="exec-net")
    )
    assert allowed.status == "failed"
    # With network allowed the failure is a plain connection refusal, not EPERM.
    assert "Operation not permitted" not in allowed.error_message
