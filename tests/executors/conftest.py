from __future__ import annotations

from pathlib import Path

import pytest

from server.app.executors.models import ExecutionContext
from server.app.skills.manager import SkillManager
from tests.helpers.skill_manager import _make_skill_manager


@pytest.fixture
def skill_manager(tmp_path: Path) -> SkillManager:
    return _make_skill_manager(
        tmp_path,
        "demo_video_workflow/gen",
        validate_script="#!/usr/bin/env python3\n",
    )


@pytest.fixture
def execution_context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="test",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="demo_video_workflow",
        node_key="gen",
        capability="cap",
        workspace={"id": "ws-a"},
        job={
            "id": "job-1",
            "workspace_id": "ws-a",
            "workflow_key": "demo_video_workflow",
            "source_type": "video",
            "source_id": "v-1",
            "batch_id": "",
            "title": "Video 1",
            "storage_dir": str(tmp_path),
            "stem": "",
        },
        job_dir=tmp_path,
        log_path=tmp_path / "run.log",
        inputs=("in.json",),
        expected_outputs=("out.json",),
    )
