import json
from pathlib import Path

import pytest

from server.app.jobs import JobQueries
from server.app.pipelines.pi_runner import PiConfig, PiRunner


def test_build_pi_command_uses_fresh_session_and_one_explicit_skill(tmp_path):
    runner = PiRunner.from_config(
        {
            "binary": "pi",
            "provider": "openai",
            "model": "gpt-5.1-codex-mini",
            "thinking": "low",
            "timeout_seconds": 600,
        },
        skill_root=tmp_path / "skills",
    )
    command = runner.build_command(
        skill_dir=tmp_path / "skills/reading_analysis/extract_keywords",
        session_dir=tmp_path / "run/session",
        tools=["read", "write", "bash"],
        session_name="job-1:extract_keywords:7",
        prompt_file=tmp_path / "run/prompt.md",
    )

    assert command[:3] == ["pi", "--mode", "json"]
    assert "--session-dir" in command
    assert "--no-skills" in command
    assert command[command.index("--skill") + 1].endswith("extract_keywords")
    assert command[command.index("--tools") + 1] == "read,write,bash"
    assert "--no-context-files" in command
    assert "--no-extensions" in command
    assert "--no-prompt-templates" in command
    assert "--approve" in command
    assert "--no-session" not in command


def test_build_pi_command_omits_empty_provider_and_model(tmp_path):
    runner = PiRunner.from_config(
        {"binary": "pi", "provider": "", "model": "", "thinking": "low"},
        skill_root=tmp_path / "skills",
    )
    command = runner.build_command(
        skill_dir=tmp_path / "skills/foo",
        session_dir=tmp_path / "run/session",
        tools=["read"],
        session_name="s",
        prompt_file=tmp_path / "run/prompt.md",
    )
    assert "--provider" not in command
    assert "--model" not in command


def test_build_pi_command_includes_provider_and_model_when_set(tmp_path):
    runner = PiRunner.from_config(
        {
            "binary": "pi",
            "provider": "openai",
            "model": "gpt-4",
            "thinking": "medium",
        },
        skill_root=tmp_path / "skills",
    )
    command = runner.build_command(
        skill_dir=tmp_path / "skills/foo",
        session_dir=tmp_path / "run/session",
        tools=["read"],
        session_name="s",
        prompt_file=tmp_path / "run/prompt.md",
    )
    assert "--provider" in command
    idx = command.index("--provider")
    assert command[idx + 1] == "openai"
    assert "--model" in command
    idx = command.index("--model")
    assert command[idx + 1] == "gpt-4"
    assert "--thinking" in command
    idx = command.index("--thinking")
    assert command[idx + 1] == "medium"


def test_piconfig_defaults():
    config = PiConfig()
    assert config.binary == "pi"
    assert config.provider == ""
    assert config.model == ""
    assert config.thinking == "low"
    assert config.timeout_seconds == 600


def test_pirunner_requires_binary():
    with pytest.raises(ValueError, match="binary"):
        PiRunner.from_config({}, skill_root=Path("/skills"))


def test_pirunner_rejects_invalid_timeout():
    with pytest.raises(ValueError, match="timeout"):
        PiRunner.from_config(
            {"binary": "pi", "timeout_seconds": 0},
            skill_root=Path("/skills"),
        )


def test_run_creates_trace_artifacts_and_returns_result(tmp_path, monkeypatch):
    # Create a fake pi executable that writes valid JSON lines and creates outputs
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text(
        "#!/bin/bash\n"
        "for last; do true; done\n"
        "# last arg is the prompt file\n"
        'echo \'{"event":"done"}\'\n'
        "# Create outputs in cwd (job_dir)\n"
        "echo '{\"questions\": []}' > keywords_raw.json\n"
        'echo \'{"questions": [], "summary": {"total": 0, "warnings": []}}\' > keywords_report.json\n'
    )
    fake_pi.chmod(0o755)

    runner = PiRunner.from_config(
        {"binary": str(fake_pi), "timeout_seconds": 10},
        skill_root=tmp_path / "skills",
    )

    # Create a fake skill with validator
    skill_dir = tmp_path / "skills/reading_analysis/extract_keywords"
    (skill_dir / "scripts").mkdir(parents=True)
    validator = skill_dir / "scripts/validate_output.py"
    validator.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "job_dir = Path(sys.argv[1])\n"
        "(job_dir / 'keywords_raw.json').write_text('{\"questions\": []}')\n"
        '(job_dir / \'keywords_report.json\').write_text(\'{"questions": [], "summary": {"total": 0, "warnings": []}}\')\n'
    )
    validator.chmod(0o755)

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    job = {"id": "default_reading_analysis_Q1", "storage_dir": str(job_dir)}

    result = runner.run(
        job=job,
        node_key="extract_keywords",
        skill_dir=skill_dir,
        inputs=["questions_parsed.json"],
        outputs=["keywords_raw.json", "keywords_report.json"],
        job_db=None,
    )

    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.run_dir.exists()
    assert (result.run_dir / "prompt.md").is_file()
    assert (result.run_dir / "events.jsonl").is_file()
    assert (result.run_dir / "stderr.log").is_file()
    assert (result.run_dir / "run.json").is_file()
    assert (result.run_dir / "session").is_dir()
    assert (job_dir / "keywords_raw.json").is_file()
    assert (job_dir / "keywords_report.json").is_file()

    run_meta = json.loads((result.run_dir / "run.json").read_text())
    assert run_meta["node_key"] == "extract_keywords"
    assert run_meta["exit_code"] == 0
    assert "start_time" in run_meta
    assert "end_time" in run_meta
    assert run_meta["start_time"] is not None
    assert run_meta["end_time"] is not None


def test_run_persists_node_run_and_finishes_it(tmp_path, monkeypatch):
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text(
        '#!/bin/bash\necho \'{"event":"done"}\'\necho \'{"questions": []}\' > keywords_raw.json\n'
    )
    fake_pi.chmod(0o755)

    runner = PiRunner.from_config(
        {"binary": str(fake_pi), "timeout_seconds": 10},
        skill_root=tmp_path / "skills",
    )

    skill_dir = tmp_path / "skills/reading_analysis/extract_keywords"
    (skill_dir / "scripts").mkdir(parents=True)
    validator = skill_dir / "scripts/validate_output.py"
    validator.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "job_dir = Path(sys.argv[1])\n"
        "(job_dir / 'keywords_raw.json').write_text('{\"questions\": []}')\n"
    )
    validator.chmod(0o755)

    db_path = tmp_path / "jobs.sqlite"
    job_db = JobQueries(db_path, tmp_path / "jobs")
    job = job_db.create_job(
        pipeline_key="reading_analysis",
        source_type="question",
        source_id="Q1",
        batch_id="b1",
        title="Q1",
        node_keys=["extract_keywords"],
    )
    job_dir = Path(job["storage_dir"])
    job_dir.mkdir(parents=True, exist_ok=True)

    result = runner.run(
        job=job,
        node_key="extract_keywords",
        skill_dir=skill_dir,
        inputs=["questions_parsed.json"],
        outputs=["keywords_raw.json"],
        job_db=job_db,
    )

    assert result.status == "completed"
    runs = job_db.list_node_runs(job["id"])
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["exit_code"] == 0
    assert runs[0]["run_dir"] == str(result.run_dir)
    assert runs[0]["session_dir"] == str(result.session_dir)
    assert json.loads(runs[0]["command_json"])[0] == str(fake_pi)

    node = job_db.get_job_node(job["id"], "extract_keywords")
    assert node["status"] == "completed"


def test_run_fails_when_output_missing(tmp_path, monkeypatch):
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text('#!/bin/bash\necho \'{"event":"done"}\'\n')
    fake_pi.chmod(0o755)

    runner = PiRunner.from_config(
        {"binary": str(fake_pi), "timeout_seconds": 10},
        skill_root=tmp_path / "skills",
    )

    skill_dir = tmp_path / "skills/reading_analysis/extract_keywords"
    (skill_dir / "scripts").mkdir(parents=True)
    validator = skill_dir / "scripts/validate_output.py"
    validator.write_text("#!/usr/bin/env python3\nimport sys\n")
    validator.chmod(0o755)

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    job = {"id": "default_reading_analysis_Q1", "storage_dir": str(job_dir)}

    result = runner.run(
        job=job,
        node_key="extract_keywords",
        skill_dir=skill_dir,
        inputs=["questions_parsed.json"],
        outputs=["keywords_raw.json", "keywords_report.json"],
        job_db=None,
    )

    assert result.status == "failed"
    assert result.exit_code != 0


def test_run_fails_when_binary_missing(tmp_path):
    runner = PiRunner.from_config(
        {"binary": str(tmp_path / "nonexistent"), "timeout_seconds": 10},
        skill_root=tmp_path / "skills",
    )

    skill_dir = tmp_path / "skills/reading_analysis/extract_keywords"
    (skill_dir / "scripts").mkdir(parents=True)
    validator = skill_dir / "scripts/validate_output.py"
    validator.write_text("#!/usr/bin/env python3\nimport sys\n")
    validator.chmod(0o755)

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    job = {"id": "default_reading_analysis_Q1", "storage_dir": str(job_dir)}

    result = runner.run(
        job=job,
        node_key="extract_keywords",
        skill_dir=skill_dir,
        inputs=["questions_parsed.json"],
        outputs=["keywords_raw.json", "keywords_report.json"],
        job_db=None,
    )

    assert result.status == "failed"
    assert result.exit_code == 127


def test_run_fails_when_validator_rejects_output(tmp_path, monkeypatch):
    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text(
        '#!/bin/bash\necho \'{"event":"done"}\'\necho \'{"bad": true}\' > keywords_raw.json\n'
    )
    fake_pi.chmod(0o755)

    runner = PiRunner.from_config(
        {"binary": str(fake_pi), "timeout_seconds": 10},
        skill_root=tmp_path / "skills",
    )

    skill_dir = tmp_path / "skills/reading_analysis/extract_keywords"
    (skill_dir / "scripts").mkdir(parents=True)
    validator = skill_dir / "scripts/validate_output.py"
    validator.write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.stderr.write('validation failed')\nsys.exit(1)\n"
    )
    validator.chmod(0o755)

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    job = {"id": "default_reading_analysis_Q1", "storage_dir": str(job_dir)}

    result = runner.run(
        job=job,
        node_key="extract_keywords",
        skill_dir=skill_dir,
        inputs=["questions_parsed.json"],
        outputs=["keywords_raw.json"],
        job_db=None,
    )

    assert result.status == "failed"
    assert "validation failed" in result.error_message
