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


def test_job_node_response_accepts_remote_executor_kind():
    node = JobNodeResponse(
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
        executor_id="pi-remote",
        executor_kind="remote",
    )
    assert node.executor_kind == "remote"
