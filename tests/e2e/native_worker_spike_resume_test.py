from scripts.e2e.native_worker_spike import WORKSPACE_ID
from scripts.e2e.native_worker_spike_control import resume_workspace
from server.app.worker_control import WorkspaceWorkerControl


def test_real_app_startup_requires_resume_before_worker_claim(client_factory, job_db) -> None:
    control = WorkspaceWorkerControl(job_db)
    control.resume(WORKSPACE_ID)
    assert control.is_paused(WORKSPACE_ID) is False

    with client_factory(authenticated=False, fresh=True) as client:
        resume_workspace(client, "", WORKSPACE_ID)
        assert client.app.state.workspace_worker_control.is_paused(WORKSPACE_ID) is False
