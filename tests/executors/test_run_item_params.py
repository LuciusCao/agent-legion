"""Host code nodes read generic item params through the existing SDK payload (#1565)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from server.app.executors.code import CodeExecutor
from server.app.executors.models import ExecutionContext
from tests.helpers.velites_sandbox import sandboxed

REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_db


class _JobDb:
    dsn_identity = "postgresql://fake"
    jobs_dir = "/fake"

    def get_run(self, run_id: str) -> dict | None:
        if run_id != "run-params":
            return None
        return {"id": run_id, "frozen_pins_json": "{}"}

    def list_node_runs(self, job_id: str) -> list[dict]:
        return []


def test_sandboxed_host_node_reads_frozen_material_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandboxed(monkeypatch)
    params = {"variant": "蓝色", "options": {"indexes": [1, 2], "enabled": True}}
    input_doc = {"type": "material", "material_id": "mat-params", "params": params}
    context = ExecutionContext(
        execution_id="exec-params",
        lease_id="lease-params",
        node_run_id=1,
        executor_id="code",
        workspace_id="ws-params",
        job_id="job-params",
        workflow_key="ws-params",
        node_key="consume",
        capability="consume",
        workspace={"id": "ws-params"},
        job={
            "id": "job-params",
            "workspace_id": "ws-params",
            "workflow_key": "ws-params",
            "run_id": "run-params",
            "input_json": json.dumps(input_doc, ensure_ascii=False),
        },
        job_dir=tmp_path,
        log_path=tmp_path / "run.log",
        inputs=(),
        expected_outputs=("output.json",),
        node_config={"safe_business_value": "from-node-config"},
        node_code=textwrap.dedent(
            """
            from workspace_libs.node_sdk import NodeContext, entrypoint

            @entrypoint
            def run(ctx: NodeContext) -> None:
                candidate = ctx.batch_payload["task_candidates"][0]
                ctx.artifacts.write_json(
                    "output.json",
                    {"params": candidate["params"], "node_config": dict(ctx.config)},
                )
            """
        ),
    )
    monkeypatch.setattr(
        "server.app.executors._code_runtime.prefetch_material_block",
        lambda executor, job, workspace_id: None,
    )

    result = CodeExecutor(repo_root=REPO_ROOT, job_db=_JobDb()).execute(context)

    assert result.status == "completed", result.error_message
    output = json.loads((tmp_path / "output.json").read_text(encoding="utf-8"))
    assert output == {
        "params": params,
        "node_config": {"safe_business_value": "from-node-config"},
    }
