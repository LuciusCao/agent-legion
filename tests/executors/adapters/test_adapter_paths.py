from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from server.app.executors.code import CodeExecutor
from server.app.executors.config import (
    CodeCapabilityConfig,
    OpenClawCapabilityConfig,
    PiCapabilityConfig,
)
from server.app.executors.models import ExecutionContext
from server.app.executors.openclaw import OpenClawExecutor
from server.app.executors.openclaw_runner import OpenClawRunner
from server.app.executors.pi import PiExecutor
from server.app.executors.runtime_config import PiRuntimeConfig
from tests.executors.adapters.helpers import _make_skill_manager


def test_code_executor_result_log_path_is_absolute(
    tmp_path: Path, context: ExecutionContext
) -> None:
    node = tmp_path / "node_write_output.py"
    node.write_text(
        "def run(job, job_dir, runtime):\n"
        "    (job_dir / 'out.json').write_text('{}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    executor = CodeExecutor(
        "code-default",
        {"fetch": CodeCapabilityConfig(path="node_write_output.py")},
        repo_root=tmp_path,
    )
    result = executor.execute(replace(context, capability="fetch", expected_outputs=("out.json",)))
    assert result.status == "completed"
    assert result.log_path == str(context.log_path)
    assert Path(result.log_path).is_absolute()


def test_pi_executor_result_log_path_is_absolute(tmp_path: Path) -> None:
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text(
        '#!/bin/bash\necho \'{"event":"done"}\'\necho \'{"questions": []}\' > keywords_raw.json\n'
    )
    fake_pi.chmod(0o755)

    skill_manager = _make_skill_manager(
        tmp_path,
        "demo_workflow/generate_key_info",
        validate_script=(
            "#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n"
            "job_dir = Path(sys.argv[1])\n"
            "(job_dir / 'keywords_raw.json').write_text('{\"questions\": []}')\n"
        ),
    )

    executor = PiExecutor(
        "pi-default",
        PiRuntimeConfig(binary=str(fake_pi)),
        skill_manager,
        {"extract_keywords": PiCapabilityConfig(skill="demo_workflow/generate_key_info")},
    )

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    ctx = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="pi-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="demo_workflow",
        node_key="extract_keywords",
        capability="extract_keywords",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=("questions_parsed.json",),
        expected_outputs=("keywords_raw.json",),
    )

    result = executor.execute(ctx)
    assert result.status == "completed"
    assert result.log_path == str(ctx.log_path)
    assert Path(result.log_path).is_absolute()


def test_openclaw_executor_result_log_path_is_absolute(tmp_path: Path) -> None:
    command = [
        "python3",
        "-c",
        (
            "import pathlib, sys; "
            "out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); "
            "(out / 'interactions.json').write_text('{}', encoding='utf-8')"
        ),
        "{video_dir}",
    ]
    runner = OpenClawRunner(command_template=command, cwd=tmp_path, timeout_seconds=10)
    executor = OpenClawExecutor(
        "oc-default",
        runner,
        {"interaction_generate": OpenClawCapabilityConfig(skill="interaction")},
    )

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    ctx = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="oc-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="demo_workflow",
        node_key="interaction_generate",
        capability="interaction_generate",
        workspace={"id": "ws-a"},
        job={"id": "job-1", "storage_dir": str(job_dir)},
        job_dir=job_dir,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=("interactions.json",),
    )

    result = executor.execute(ctx)
    assert result.status == "completed"
    assert result.log_path == str(ctx.log_path)
    assert Path(result.log_path).is_absolute()
