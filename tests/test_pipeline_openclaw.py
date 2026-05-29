from server.app.pipeline.openclaw import AgentPhase, OpenClawRunner


def test_openclaw_runner_sanitizes_null_bytes_in_prompt(tmp_path):
    """Prompt text containing null bytes should be sanitized before substitution."""
    command = [
        "python3",
        "-c",
        "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('ok', encoding='utf-8')",
        "{prompt_text}",
    ]
    runner = OpenClawRunner(command_template=command, cwd=tmp_path, timeout_seconds=10)
    # Inject a prompt file with embedded null bytes
    prompt_file = tmp_path / "bad.md"
    prompt_file.write_text("hello\x00world", encoding="utf-8")
    rendered = runner.render_command(video_id="v1", video_dir=tmp_path, prompt_file=prompt_file)
    prompt_arg = rendered[-1]
    assert "\x00" not in prompt_arg
    assert prompt_arg == "helloworld"


def test_openclaw_runner_escapes_shell_metacharacters_in_prompt(tmp_path):
    runner = OpenClawRunner(
        command_template=["cmd", "--message", "{prompt_text}"],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    prompt_file = tmp_path / "test.md"
    prompt_file.write_text("hello; rm -rf /", encoding="utf-8")
    rendered = runner.render_command(video_id="v1", video_dir=tmp_path, prompt_file=prompt_file)
    assert rendered == ["cmd", "--message", "'hello; rm -rf /'"]


def test_openclaw_runner_escapes_dollar_command_substitution(tmp_path):
    runner = OpenClawRunner(
        command_template=["cmd", "--msg", "{prompt_text}"],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    prompt_file = tmp_path / "test.md"
    prompt_file.write_text("$(echo pwned)", encoding="utf-8")
    rendered = runner.render_command(video_id="v1", video_dir=tmp_path, prompt_file=prompt_file)
    assert rendered == ["cmd", "--msg", "'$(echo pwned)'"]


def test_openclaw_runner_escapes_backtick_command_substitution(tmp_path):
    runner = OpenClawRunner(
        command_template=["cmd", "--msg", "{prompt_text}"],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    prompt_file = tmp_path / "test.md"
    prompt_file.write_text("`echo pwned`", encoding="utf-8")
    rendered = runner.render_command(video_id="v1", video_dir=tmp_path, prompt_file=prompt_file)
    assert rendered == ["cmd", "--msg", "'`echo pwned`'"]


def test_openclaw_runner_preserves_normal_text(tmp_path):
    runner = OpenClawRunner(
        command_template=["cmd", "--message", "{prompt_text}"],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    prompt_file = tmp_path / "test.md"
    prompt_file.write_text("Hello world! 你好。", encoding="utf-8")
    rendered = runner.render_command(video_id="v1", video_dir=tmp_path, prompt_file=prompt_file)
    assert rendered == ["cmd", "--message", "Hello world! 你好。"]


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
