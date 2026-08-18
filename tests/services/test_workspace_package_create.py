import json
import zipfile

from server.app.services.workspace_package_create import create_workspace_package


def test_create_workspace_package_includes_manifest_and_artifacts(tmp_path):
    jobs_dir = tmp_path / "jobs"
    packages_dir = tmp_path / "packages"
    job_dir = jobs_dir / "job_1"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(json.dumps({"answer": 42}), encoding="utf-8")

    jobs = [
        {
            "id": "job_1",
            "source_id": "S1",
            "workflow_key": "demo_workflow",
            "status": "completed",
        }
    ]

    package_path, job_count = create_workspace_package(jobs, packages_dir, jobs_dir)

    assert package_path.exists()
    assert job_count == 1
    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "job_1/result.json" in names
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["jobs"] == [
            {
                "id": "job_1",
                "source_id": "S1",
                "workflow_key": "demo_workflow",
                "workflow": {
                    "key": "demo_workflow",
                    "version": None,
                    "revision_id": "",
                    "definition_hash": "",
                },
                "status": "completed",
            }
        ]
        artifact = json.loads(zf.read("job_1/result.json").decode("utf-8"))
        assert artifact == {"answer": 42}


def test_workspace_package_falls_back_to_jobs_base_dir_when_storage_dir_empty(tmp_path):
    jobs_dir = tmp_path / "jobs"
    packages_dir = tmp_path / "packages"
    job_dir = jobs_dir / "job_2"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    jobs = [
        {
            "id": "job_2",
            "source_id": "S2",
            "workflow_key": "demo_workflow",
            "status": "completed",
            "storage_dir": "",
        }
    ]

    package_path, job_count = create_workspace_package(jobs, packages_dir, jobs_dir)

    assert package_path.exists()
    assert job_count == 1
    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

    assert "job_2/result.json" in names
    assert manifest["jobs"][0]["workflow_key"] == "demo_workflow"


def test_workspace_package_skips_job_when_directory_missing(tmp_path):
    jobs_dir = tmp_path / "jobs"
    packages_dir = tmp_path / "packages"
    # Deliberately do not create jobs_dir / "job_3"

    jobs = [
        {
            "id": "job_3",
            "source_id": "S3",
            "workflow_key": "demo_workflow",
            "status": "completed",
            "storage_dir": "",
        }
    ]

    package_path, job_count = create_workspace_package(jobs, packages_dir, jobs_dir)

    assert package_path.exists()
    assert job_count == 0
    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

    assert "manifest.json" in names
    assert not any(name.startswith("job_3/") for name in names)
    assert manifest["jobs"][0]["status"] == "completed"


def test_workspace_package_includes_only_whitelisted_artifacts(tmp_path):
    jobs_dir = tmp_path / "jobs"
    packages_dir = tmp_path / "packages"
    job_dir = jobs_dir / "job_1"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text('{"ok": true}', encoding="utf-8")
    (job_dir / "metadata.json").write_text('{"meta": 1}', encoding="utf-8")
    (job_dir / "report.md").write_text("report", encoding="utf-8")
    (job_dir / "extra.log").write_text("log", encoding="utf-8")
    (job_dir / "AGENTS.md").write_text("agent", encoding="utf-8")
    (job_dir / "BOOTSTRAP.md").write_text("bootstrap", encoding="utf-8")
    (job_dir / ".openclaw").mkdir()
    (job_dir / ".openclaw" / "state.json").write_text("{}", encoding="utf-8")
    (job_dir / "runs").mkdir()
    (job_dir / "runs" / "node_a").mkdir()
    (job_dir / "runs" / "node_a" / "run.json").write_text("{}", encoding="utf-8")

    jobs = [
        {
            "id": "job_1",
            "source_id": "S1",
            "workflow_key": "demo_workflow",
            "status": "completed",
        }
    ]

    package_path, job_count = create_workspace_package(jobs, packages_dir, jobs_dir)

    assert job_count == 1
    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "job_1/result.json" in names
        assert "job_1/metadata.json" in names
        assert "job_1/report.md" in names
        assert "job_1/extra.log" not in names
        assert "job_1/AGENTS.md" not in names
        assert "job_1/BOOTSTRAP.md" not in names
        assert not any(name.startswith("job_1/.openclaw/") for name in names)
        assert not any(name.startswith("job_1/runs/") for name in names)
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["jobs"][0]["id"] == "job_1"
        assert manifest["jobs"][0]["source_id"] == "S1"
        assert manifest["jobs"][0]["workflow_key"] == "demo_workflow"


def test_workspace_package_explicit_artifact_names(tmp_path):
    jobs_dir = tmp_path / "jobs"
    packages_dir = tmp_path / "packages"
    job_dir = jobs_dir / "job_1"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text('{"ok": true}', encoding="utf-8")
    (job_dir / "publish_payload.json").write_text('{"items": []}', encoding="utf-8")

    jobs = [
        {
            "id": "job_1",
            "source_id": "S1",
            "workflow_key": "demo_workflow",
            "status": "completed",
        }
    ]

    package_path, job_count = create_workspace_package(
        jobs, packages_dir, jobs_dir, artifact_names=["publish_payload.json"]
    )

    assert job_count == 1
    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
        assert "job_1/publish_payload.json" in names
        assert "job_1/result.json" not in names


def test_create_workspace_package_human_readable_name_and_collision_suffix(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    import server.app.services.workspace_package_create as wp

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 27, 12, 0, 0, tzinfo=tz or UTC)

    monkeypatch.setattr(wp, "datetime", _FixedDatetime)

    jobs_dir = tmp_path / "jobs"
    packages_dir = tmp_path / "packages"
    job_dir = jobs_dir / "job_1"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(json.dumps({"answer": 42}), encoding="utf-8")

    jobs = [{"id": "job_1", "source_id": "S1", "workflow_key": "wf", "status": "completed"}]

    first, _ = create_workspace_package(jobs, packages_dir, jobs_dir)
    second, _ = create_workspace_package(jobs, packages_dir, jobs_dir)

    assert first.name == "workspace-jobs-20260727_120000.zip"
    assert second.name == "workspace-jobs-20260727_120000-1.zip"
    assert first.exists() and second.exists()
