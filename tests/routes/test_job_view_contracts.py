import pytest
from pydantic import ValidationError

from server.app.routes.job_view_contracts import JobNodeResponse, NodeRunResponse


def test_node_run_response_has_runner_default():
    run = NodeRunResponse(
        id=1,
        job_id="j1",
        node_key="n1",
        status="completed",
        started_at="2026-07-18 00:00:00.000000",
        command_json="[]",
        log_path="",
        error_message="",
        run_dir="",
        session_dir="",
    )
    assert run.runner == ""


def _node_kwargs(**overrides):
    base = dict(
        id=1,
        job_id="j1",
        node_key="n1",
        status="running",
        stale_reason="",
        error_message="",
        created_at="2026-07-18 00:00:00.000000",
        label="n1",
        capability="cap_a",
        after=[],
        inputs=[],
        outputs=[],
    )
    return {**base, **overrides}


@pytest.mark.parametrize("execution_type", ["code", "agent", None])
def test_job_node_response_keeps_execution_type_and_identities_separate(execution_type):
    node = JobNodeResponse(
        **_node_kwargs(
            execution_type=execution_type,
            executor_kind="code",
            agent_id="key-info-generator",
            worker_id="home-mac-mini-1",
        )
    )
    assert node.execution_type == execution_type
    assert node.executor_kind == "code"
    assert node.agent_id == "key-info-generator"
    assert node.worker_id == "home-mac-mini-1"


def test_job_node_response_execution_type_defaults_to_none():
    assert JobNodeResponse(**_node_kwargs()).execution_type is None


@pytest.mark.parametrize("retired", ["pi", "openclaw"])
def test_job_node_response_rejects_retired_executor_kinds(retired):
    # #731: pi/openclaw retired with their runtimes (#75); the runtime
    # projection only ever produces "code" or None, so the contract refuses
    # the retired values instead of advertising them to API/MCP consumers.
    with pytest.raises(ValidationError):
        JobNodeResponse(**_node_kwargs(executor_kind=retired))


@pytest.mark.parametrize("invalid", ["pi", "openclaw", "remote"])
def test_job_node_response_rejects_unknown_execution_types(invalid):
    with pytest.raises(ValidationError):
        JobNodeResponse(**_node_kwargs(execution_type=invalid))
