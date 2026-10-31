import json

from cryptography.fernet import Fernet

from scripts.e2e.native_worker_spike import WORKSPACE_ID, _enqueue_worker_jobs, _settings
from scripts.e2e.native_worker_spike_control import resume_workspace
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflows.output_validation import run_output_validator
from tests.postgres_support import TEST_DATABASE_URL


def test_real_app_startup_requires_resume_before_worker_claim(client_factory, job_db) -> None:
    control = WorkspaceWorkerControl(job_db)
    control.resume(WORKSPACE_ID)
    assert control.is_paused(WORKSPACE_ID) is False

    with client_factory(authenticated=False, fresh=True) as client:
        resume_workspace(client, "", WORKSPACE_ID)
        assert client.app.state.workspace_worker_control.is_paused(WORKSPACE_ID) is False


def test_agent_enqueue_uses_node_profile_and_real_skill_repo(tmp_path, job_db, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    settings = _settings(TEST_DATABASE_URL, tmp_path / "host-data")
    for path in (settings.data_dir, settings.logs_dir, settings.jobs_dir):
        path.mkdir(parents=True, exist_ok=True)

    _enqueue_worker_jobs(
        dsn=TEST_DATABASE_URL,
        settings=settings,
        code_config={},
        code_allow_config={},
        agent_config={},
    )

    with job_db.connect() as conn:
        row = conn.execute(
            "select profile_source, runtime, agent_id, manifest_json"
            " from agent_execution_requests where job_id='worker-agent'"
        ).fetchone()
    assert row["profile_source"] == "node"
    assert row["runtime"] == "velites"
    assert row["agent_id"] == "agent"
    manifest = json.loads(row["manifest_json"])
    assert manifest["skill"] == "native-spike/probe"
    assert manifest["tools"] == ["read", "write", "bash"]
    assert len(manifest["skill_commit"]) == 40
    skill_repo = tmp_path / ".agents" / "skills" / "native-spike" / "probe"
    assert (skill_repo / ".git").is_dir()
    assert (skill_repo / "references" / "output-contract.md").is_file()
    assert (skill_repo / "scripts" / "validate_output.py").is_file()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "agent_result.json").write_text('{"agent":"native-stub","complete":true}')
    (outputs / "agent_bash_evidence.json").write_text('{"checks":{"probe":true}}')
    assert run_output_validator(skill_repo, outputs, timeout_seconds=5) is None
