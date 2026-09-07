from __future__ import annotations

import json
from pathlib import Path

from server.app.workflows.pi_protocol import (
    build_platform_envelope,
    build_prompt,
    detect_model_error,
    render_command_spec,
)

MANIFEST = {
    "job_id": "job-1",
    "node_key": "gen",
    "capability": "generate",
    "runtime": "pi",
    "inputs": ["a.txt"],
    "expected_outputs": ["out.json"],
    "additional_prompt": "be careful",
    "tools": ["read", "write"],
    "skill": "demo_video_workflow/gen",
    "skill_version": "v1",
    "run_token": "tok123",
    "execution": {
        "binary": "pi",
        "provider": "p",
        "model": "m",
        "thinking": "high",
        "timeout_seconds": 300,
        "no_sandbox": False,
    },
}


def test_build_prompt_contains_all_sections(tmp_path: Path) -> None:
    prompt = build_prompt(MANIFEST, job_dir=tmp_path / "job", skill_dir=tmp_path / "skill")
    assert "Job ID: job-1" in prompt
    assert "Node: gen" in prompt
    assert "- a.txt" in prompt
    assert "- out.json" in prompt
    # #513：无 prompt_mode → append（默认指令 + 自定义内容都进提示词）。
    assert "Your task: gen" in prompt
    assert prompt.endswith("\nbe careful\n")
    assert "Additional node instructions" not in prompt
    assert (
        "Do not read, search, or modify anything outside the working directory "
        "and the skill directory." in prompt
    )
    assert prompt.endswith("\n")


def test_build_prompt_overwrite_mode_replaces_default_instructions(
    tmp_path: Path,
) -> None:
    """#513：overwrite = 自定义内容整段替换默认指令（旧行为显式选择）。"""
    manifest = {**MANIFEST, "prompt_mode": "overwrite"}
    prompt = build_prompt(manifest, job_dir=tmp_path / "job", skill_dir=tmp_path / "skill")
    assert "Node instructions:\nbe careful\n" in prompt
    assert "Your task:" not in prompt


def test_build_prompt_append_mode_splices_default_and_custom(tmp_path: Path) -> None:
    """#513：append = 默认指令在前、自定义内容空行拼接在后。"""
    manifest = {**MANIFEST, "prompt_mode": "append"}
    prompt = build_prompt(manifest, job_dir=tmp_path / "job", skill_dir=tmp_path / "skill")
    assert "Your task: gen (capability `generate`)" in prompt
    assert prompt.endswith("working directory.\n\nbe careful\n")


def test_build_prompt_empty_prompt_selects_default_instructions(tmp_path: Path) -> None:
    manifest = {**MANIFEST, "additional_prompt": ""}
    prompt = build_prompt(manifest, job_dir=tmp_path / "job", skill_dir=tmp_path / "skill")
    # 空 prompt → 自动组装的默认指令（label 缺失时回落 node_key）。
    assert "Your task: gen (capability `generate`)" in prompt
    assert "`demo_video_workflow/gen`" in prompt


def test_platform_envelope_excludes_node_instructions(tmp_path: Path) -> None:
    """#513：信封半区不含节点指令——平台提示词面板与编辑区各自呈现。"""
    envelope = build_platform_envelope(
        MANIFEST, job_dir=tmp_path / "job", skill_dir=tmp_path / "skill"
    )
    assert "Job ID: job-1" in envelope
    assert "Required outputs:" in envelope
    # 节点指令三种形态（自定义/默认组装）都不进信封。
    assert "Node instructions:" not in envelope
    assert "be careful" not in envelope
    assert "Your task:" not in envelope
    # 拼接不回归（append 默认）：build_prompt = 信封 + 节点指令段（默认
    # 指令 + 自定义内容），dispatch 消费不变。
    full = build_prompt(MANIFEST, job_dir=tmp_path / "job", skill_dir=tmp_path / "skill")
    assert full.startswith(envelope)
    assert "Node instructions:\n" in full
    assert full.endswith("working directory.\n\nbe careful\n")


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
