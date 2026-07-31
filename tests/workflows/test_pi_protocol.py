from __future__ import annotations

import json
from pathlib import Path

from server.app.workflows.pi_protocol import (
    build_command,
    build_prompt,
    detect_model_error,
    render_command_spec,
)

MANIFEST = {
    "job_id": "job-1",
    "node_key": "gen",
    "capability": "generate",
    "inputs": ["a.txt"],
    "expected_outputs": ["out.json"],
    "additional_prompt": "be careful",
    "tools": ["read", "write"],
    "skill": "video_knowledge/gen",
    "skill_version": "v1",
    "run_token": "tok123",
    "pi": {
        "binary": "pi",
        "provider": "p",
        "model": "m",
        "thinking": "high",
        "timeout_seconds": 300,
        "environment": {},
    },
}


def test_build_prompt_contains_all_sections(tmp_path: Path) -> None:
    prompt = build_prompt(MANIFEST, job_dir=tmp_path / "job", skill_dir=tmp_path / "skill")
    assert "Job ID: job-1" in prompt
    assert "Node: gen" in prompt
    assert "- a.txt" in prompt
    assert "- out.json" in prompt
    assert "Additional node instructions:\nbe careful" in prompt
    assert (
        "Do not read, search, or modify anything outside the working directory "
        "and the skill directory." in prompt
    )
    assert prompt.endswith("\n")


def test_build_prompt_matches_legacy_builder(tmp_path: Path) -> None:
    # 与 pi_prompt.build_pi_prompt 逐字符等价（同一 job_dir/skill_dir/validator 输入）。
    from server.app.workflows.pi_prompt import build_pi_prompt

    job_dir, skill_dir = tmp_path / "job", tmp_path / "skill"
    legacy = build_pi_prompt(
        job_id="job-1",
        node_key="gen",
        job_dir=job_dir,
        skill_dir=skill_dir,
        validator_script=skill_dir / "scripts" / "validate_output.py",
        inputs=["a.txt"],
        outputs=["out.json"],
        additional_prompt="be careful",
    )
    assert build_prompt(MANIFEST, job_dir=job_dir, skill_dir=skill_dir) == legacy


def test_build_command_matches_legacy_builder(tmp_path: Path) -> None:
    from server.app.workflows.pi_command_builder import build_pi_command
    from server.app.workflows.pi_config import PiConfig

    config = PiConfig(binary="pi", provider="p", model="m", thinking="high")
    skill_dir, session_dir = tmp_path / "skill", tmp_path / "s"
    prompt_file = tmp_path / "prompt.md"
    legacy = build_pi_command(
        config,
        skill_dir=skill_dir,
        session_dir=session_dir,
        tools=["read", "write"],
        session_name="job-1:gen:tok123",
        prompt_file=prompt_file,
    )
    got = build_command(
        MANIFEST,
        skill_dir=skill_dir,
        session_dir=session_dir,
        session_name="job-1:gen:tok123",
        prompt_file=prompt_file,
    )
    assert got == legacy


def test_detect_model_error_finds_wrapped_message(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps({"type": "message_start", "message": {"role": "assistant"}}),
                json.dumps({"message": {"errorMessage": "400 bad request"}}),
            ]
        ),
        encoding="utf-8",
    )
    assert detect_model_error(events) == "400 bad request"
    assert detect_model_error(tmp_path / "missing.jsonl") is None


def test_detect_model_error_assistant_message_event(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({"assistantMessageEvent": {"message": {"errorMessage": "boom"}}}),
        encoding="utf-8",
    )
    assert detect_model_error(events) == "boom"


def test_detect_model_error_ignores_error_recovered_by_retry(tmp_path: Path) -> None:
    # Pi auto-retries transient model errors (e.g. upstream "terminated"); once
    # a later assistant message succeeds, the run recovered and the early
    # error must not fail the node.
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "stopReason": "error",
                            "errorMessage": "terminated",
                        },
                    }
                ),
                json.dumps({"type": "auto_retry_start", "attempt": 1}),
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {"role": "assistant", "stopReason": "toolUse"},
                    }
                ),
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {"role": "assistant", "stopReason": "stop"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    assert detect_model_error(events) is None


def test_detect_model_error_reports_unrecovered_error(tmp_path: Path) -> None:
    # A successful message followed by an error with no later success is still
    # a terminal model failure.
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {"role": "assistant", "stopReason": "toolUse"},
                    }
                ),
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "stopReason": "error",
                            "errorMessage": "terminated",
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    assert detect_model_error(events) == "terminated"


def test_render_command_spec_uses_placeholders() -> None:
    spec = render_command_spec(MANIFEST)
    assert spec["version"] == 1
    assert "{job_dir}" in spec["prompt"] and "{skill_dir}" in spec["prompt"]
    assert spec["command"][0] == "pi"
    assert any("{session_dir}" in part for part in spec["command"])
    assert any(
        part.endswith("{prompt_file}") or "{prompt_file}" in part for part in spec["command"]
    )
    assert spec["prompt_instruction"] == "Execute the attached node instructions."
