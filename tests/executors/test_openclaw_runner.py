import subprocess
from unittest.mock import patch

from server.app.executors.agent_workspace import cleanup_agent_workspace_files
from server.app.executors.openclaw_runner import (
    AgentPhase,
    OpenClawRunner,
    SkillSafetyConfig,
    restore_skill_repos,
)


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


def test_openclaw_runner_cleans_agent_workspace_files_from_video_dir(tmp_path):
    command = [
        "python3",
        "-c",
        (
            "import json, pathlib, sys; "
            "out=pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); "
            "(out/'interactions.json').write_text(json.dumps({'interactions':[]}), encoding='utf-8'); "
            "(out/'AGENTS.md').write_text('agent workspace', encoding='utf-8'); "
            "(out/'BOOTSTRAP.md').write_text('bootstrap', encoding='utf-8'); "
            "(out/'.openclaw').mkdir(exist_ok=True); "
            "(out/'.openclaw'/'workspace-state.json').write_text('{}', encoding='utf-8')"
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
    assert not (tmp_path / "video" / "AGENTS.md").exists()
    assert not (tmp_path / "video" / "BOOTSTRAP.md").exists()
    assert not (tmp_path / "video" / ".openclaw").exists()


def test_openclaw_runner_uses_and_removes_isolated_workspace(tmp_path):
    command = [
        "python3",
        "-c",
        (
            "import json, pathlib, sys; "
            "cwd=pathlib.Path.cwd(); "
            "(cwd/'AGENTS.md').write_text('pollution', encoding='utf-8'); "
            "out=pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); "
            "(out/'interactions.json').write_text(json.dumps({'interactions':[]}), encoding='utf-8')"
        ),
        "{video_dir}",
    ]
    workspace_root = tmp_path / "workspaces"
    runner = OpenClawRunner(
        command_template=command,
        cwd=tmp_path,
        timeout_seconds=10,
        isolated_workspace_root=workspace_root,
    )
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
    assert not (tmp_path / "AGENTS.md").exists()
    assert list(workspace_root.iterdir()) == []


def test_cleanup_agent_workspace_files_removes_only_known_pollution(tmp_path):
    video_dir = tmp_path / "video"
    video_dir.mkdir()
    (video_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (video_dir / "AGENTS.md").write_text("agent", encoding="utf-8")
    (video_dir / "MEMORY.md").write_text("memory", encoding="utf-8")
    (video_dir / ".openclaw").mkdir()
    (video_dir / ".openclaw" / "workspace-state.json").write_text("{}", encoding="utf-8")

    removed = cleanup_agent_workspace_files(video_dir)

    assert {path.name for path in removed} == {"AGENTS.md", "MEMORY.md", ".openclaw"}
    assert (video_dir / "metadata.json").exists()
    assert not (video_dir / "AGENTS.md").exists()
    assert not (video_dir / ".openclaw").exists()


def test_restore_skill_repos_checkouts_and_cleans(tmp_path):
    """restore_skill_repos should force-checkout to the given ref and clean untracked files."""
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    # 清除 GIT_* 环境变量，防止在 pre-commit hook 等场景中污染外部 git 仓库
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init"], cwd=str(repo), env=clean_env, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo),
        env=clean_env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        env=clean_env,
        check=True,
        capture_output=True,
    )

    tracked = repo / "tracked.txt"
    tracked.write_text("v1", encoding="utf-8")
    subprocess.run(
        ["git", "add", "."], cwd=str(repo), env=clean_env, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "v1"], cwd=str(repo), env=clean_env, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "tag", "v1.0.0"], cwd=str(repo), env=clean_env, check=True, capture_output=True
    )

    # dirty the repo
    tracked.write_text("dirty", encoding="utf-8")
    untracked = repo / "untracked.txt"
    untracked.write_text("garbage", encoding="utf-8")

    restore_skill_repos([{"path": str(repo), "ref": "v1.0.0"}])

    assert tracked.read_text(encoding="utf-8") == "v1"
    assert not untracked.exists()


def test_restore_skill_repos_skips_non_git_directories(tmp_path, caplog):
    """restore_skill_repos should skip paths that are not git repos."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    with caplog.at_level("WARNING"):
        restore_skill_repos([{"path": str(not_a_repo), "ref": "v1.0.0"}])
    assert "not a git repo" in caplog.text


def test_openclaw_runner_calls_restore_before_run(tmp_path):
    """If skill_safety is enabled, runner.run() should call restore_skill_repos before executing."""
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
    safety = SkillSafetyConfig(enabled=True, repos=[{"path": str(tmp_path), "ref": "v1.0.0"}])
    runner = OpenClawRunner(
        command_template=command,
        cwd=tmp_path,
        timeout_seconds=10,
        skill_safety=safety,
    )
    phase = AgentPhase(
        key="interaction_generate",
        reference_path=tmp_path / "reference.md",
        expected_outputs=["interactions.json"],
        json_outputs=["interactions.json"],
    )
    (tmp_path / "reference.md").write_text("Generate interactions.", encoding="utf-8")

    with patch("server.app.executors.openclaw_runner.restore_skill_repos") as mock_restore:
        result = runner.run(
            phase=phase,
            video_id="a",
            video_dir=tmp_path / "video",
            prompt_dir=tmp_path / "prompts",
            log_path=tmp_path / "run.log",
        )
        mock_restore.assert_called_once_with(safety.repos)

    assert result.status == "completed"
