import json
import zipfile

from server.app.pipeline.package import create_workspace_package


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
            "workflow_key": "test_pipeline",
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
                "workflow_key": "test_pipeline",
                "workflow": {
                    "key": "test_pipeline",
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
            "workflow_key": "question_comprehension_info",
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
    assert manifest["jobs"][0]["workflow_key"] == "question_comprehension_info"


def test_workspace_package_skips_job_when_directory_missing(tmp_path):
    jobs_dir = tmp_path / "jobs"
    packages_dir = tmp_path / "packages"
    # Deliberately do not create jobs_dir / "job_3"

    jobs = [
        {
            "id": "job_3",
            "source_id": "S3",
            "workflow_key": "question_comprehension_info",
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


def test_workspace_package_includes_comprehension_info(tmp_path):
    jobs_dir = tmp_path / "jobs"
    packages_dir = tmp_path / "packages"
    job_dir = jobs_dir / "reading_Q100"
    job_dir.mkdir(parents=True)
    (job_dir / "comprehension_info.json").write_text(
        json.dumps(
            {
                "question_id": "Q100",
                "fingerprint": None,
                "fingerprint_missing": True,
                "comprehension_data": {
                    "fingerprint": None,
                    "comprehension_difficulty": 65,
                    "key_info_list": [],
                    "possible_error_list": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    jobs = [
        {
            "id": "reading_Q100",
            "source_id": "Q100",
            "workflow_key": "question_comprehension_info",
            "status": "completed",
        }
    ]

    package_path, job_count = create_workspace_package(jobs, packages_dir, jobs_dir)

    assert job_count == 1
    assert package_path.exists()
    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
        assert "reading_Q100/comprehension_info.json" in names
        payload = json.loads(zf.read("reading_Q100/comprehension_info.json").decode("utf-8"))

    assert payload["question_id"] == "Q100"
    assert payload["fingerprint_missing"] is True
    assert payload["comprehension_data"]["comprehension_difficulty"] == 65
    assert payload["comprehension_data"]["key_info_list"] == []
    assert payload["comprehension_data"]["possible_error_list"] == []


def test_workspace_package_includes_only_whitelisted_artifacts(tmp_path):
    jobs_dir = tmp_path / "jobs"
    packages_dir = tmp_path / "packages"
    job_dir = jobs_dir / "job_1"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text('{"ok": true}', encoding="utf-8")
    (job_dir / "comprehension_info.json").write_text('{"q": 1}', encoding="utf-8")
    (job_dir / "question_context.json").write_text('{"ctx": 1}', encoding="utf-8")
    (job_dir / "questions.json").write_text('{"questions": []}', encoding="utf-8")
    (job_dir / "upload_params.json").write_text('{"up": 1}', encoding="utf-8")
    (job_dir / "metadata.json").write_text('{"meta": 1}', encoding="utf-8")
    (job_dir / "report.md").write_text("report", encoding="utf-8")
    (job_dir / "extra.log").write_text("log", encoding="utf-8")
    (job_dir / "AGENTS.md").write_text("agent", encoding="utf-8")
    (job_dir / "BOOTSTRAP.md").write_text("bootstrap", encoding="utf-8")
    (job_dir / ".openclaw").mkdir()
    (job_dir / ".openclaw" / "state.json").write_text("{}", encoding="utf-8")
    (job_dir / "runs").mkdir()
    (job_dir / "runs" / "extract_keywords").mkdir()
    (job_dir / "runs" / "extract_keywords" / "run.json").write_text("{}", encoding="utf-8")

    jobs = [
        {
            "id": "job_1",
            "source_id": "S1",
            "workflow_key": "question_comprehension_info",
            "status": "completed",
        }
    ]

    package_path, job_count = create_workspace_package(jobs, packages_dir, jobs_dir)

    assert job_count == 1
    with zipfile.ZipFile(package_path) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "job_1/result.json" in names
        assert "job_1/comprehension_info.json" in names
        assert "job_1/question_context.json" in names
        assert "job_1/questions.json" in names
        assert "job_1/upload_params.json" in names
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
        assert manifest["jobs"][0]["workflow_key"] == "question_comprehension_info"


def test_create_workspace_package_human_readable_name_and_collision_suffix(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    import server.app.pipeline.workspace_package as wp

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
