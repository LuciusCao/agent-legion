from server.app.pipeline.openclaw import AgentPhase, OpenClawRunner


def test_openclaw_runner_executes_template_and_validates_json(tmp_path):
    command = [
        "python3",
        "-c",
        (
            "import json, pathlib, sys; "
            "out=pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); "
            "(out/'interactions.json').write_text(json.dumps({'version':'1.0','interactions':[]}), encoding='utf-8')"
        ),
        "{video_dir}",
    ]
    runner = OpenClawRunner(command_template=command, cwd=tmp_path, timeout_seconds=10)
    phase = AgentPhase(
        key="interaction_generate",
        reference_path=tmp_path / "reference.md",
        expected_outputs=["interactions.json"],
        json_outputs=["interactions.json"],
    )
    (tmp_path / "reference.md").write_text("Generate interactions.", encoding="utf-8")

    result = runner.run(
        phase=phase,
        video_id="a",
        video_dir=tmp_path / "video",
        prompt_dir=tmp_path / "prompts",
        log_path=tmp_path / "run.log",
    )

    assert result.status == "completed"
    assert (tmp_path / "video" / "interactions.json").exists()
