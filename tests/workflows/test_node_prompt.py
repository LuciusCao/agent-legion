"""build_default_node_instructions：默认节点指令组装器（纯静态，无 DB）。

语义钉板：execution.prompt 为空时 build_prompt 用这段自动组装的默认指令；
非空时自定义文本整段替代（不再追加 "Additional node instructions"）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.workflows.node_prompt import build_default_node_instructions
from server.app.workflows.pi_protocol import build_prompt

pytestmark = pytest.mark.no_db


def test_default_instructions_reference_label_capability_and_skill() -> None:
    text = build_default_node_instructions(
        node_key="write_script",
        label="撰写教学视频脚本",
        capability="write_script",
        skill="education-video-problems-generation/write-script",
        inputs=["knowledge_point.json"],
        expected_outputs=["script.md"],
    )
    assert "撰写教学视频脚本" in text
    assert "capability `write_script`" in text
    assert "`education-video-problems-generation/write-script`" in text
    assert "`knowledge_point.json`" in text
    assert "`script.md`" in text


def test_default_instructions_fall_back_to_node_key_and_tolerate_empty_contract() -> None:
    text = build_default_node_instructions(
        node_key="intake",
        label="",
        capability="intake",
        skill="",
        inputs=[],
        expected_outputs=["data.json"],
    )
    # 空 label 回落 node_key；空 skill 不引用具体 key；空 inputs 显式说明。
    assert "Your task: intake" in text
    assert "loaded node skill" in text
    assert "``" not in text.replace("`intake`", "").replace("`data.json`", "")
    assert "declares no inputs" in text
    assert "`data.json`" in text


def test_default_instructions_list_multiple_inputs_and_outputs() -> None:
    text = build_default_node_instructions(
        node_key="review",
        label="Review",
        capability="review",
        skill="group/review",
        inputs=["a.json", "b.md"],
        expected_outputs=["review.json", "notes.md"],
    )
    assert "`a.json`, `b.md`" in text
    assert "`review.json`, `notes.md`" in text


def _manifest(**overrides: object) -> dict:
    manifest: dict = {
        "job_id": "job-1",
        "node_key": "gen",
        "node_label": "Generate",
        "capability": "generate",
        "skill": "question/generate",
        "inputs": ["a.txt"],
        "expected_outputs": ["out.json"],
        "additional_prompt": "",
    }
    manifest.update(overrides)
    return manifest


def test_build_prompt_uses_default_instructions_when_prompt_empty(tmp_path: Path) -> None:
    prompt = build_prompt(_manifest(), job_dir=tmp_path / "job", skill_dir=tmp_path / "skill")
    assert "Node instructions:" in prompt
    assert "Your task: Generate (capability `generate`)" in prompt
    assert "`question/generate`" in prompt
    assert "Additional node instructions" not in prompt


def test_build_prompt_custom_prompt_replaces_default_wholesale(tmp_path: Path) -> None:
    prompt = build_prompt(
        _manifest(additional_prompt="Follow the house style."),
        job_dir=tmp_path / "job",
        skill_dir=tmp_path / "skill",
    )
    assert "Node instructions:\nFollow the house style." in prompt
    # 自定义 prompt 整段替代默认指令：默认段一字不留，信封保持不变。
    assert "Your task:" not in prompt
    assert "Additional node instructions" not in prompt
    assert "Job ID: job-1" in prompt
    assert "- a.txt" in prompt and "- out.json" in prompt


def test_build_prompt_falls_back_to_node_key_without_label(tmp_path: Path) -> None:
    manifest = _manifest()
    del manifest["node_label"]
    prompt = build_prompt(manifest, job_dir=tmp_path / "job", skill_dir=tmp_path / "skill")
    assert "Your task: gen (capability `generate`)" in prompt


def test_build_prompt_keeps_placeholders_verbatim(tmp_path: Path) -> None:
    prompt = build_prompt(_manifest(), job_dir=Path("{job_dir}"), skill_dir=Path("{skill_dir}"))
    assert "Working directory: {job_dir}" in prompt
    assert "Skill directory: {skill_dir}" in prompt
    assert "Validator script: {skill_dir}/scripts/validate_output.py" in prompt
    assert prompt.endswith("\n")
