from __future__ import annotations

from pathlib import Path

from server.app.executors._shard_contract import (
    SHARD_OUTPUT_NAME,
    read_shard_output,
    shard_prompt_section,
)


def test_shard_prompt_section_renders_index_input_and_output_contract() -> None:
    section = shard_prompt_section({"shard_index": 2, "shard_input": {"q": 5}})
    assert "Shard index: 2" in section
    assert '"q": 5' in section
    assert SHARD_OUTPUT_NAME in section


def test_non_shard_has_no_prompt_or_output(tmp_path: Path) -> None:
    (tmp_path / SHARD_OUTPUT_NAME).write_text('{"r": 1}', encoding="utf-8")
    assert shard_prompt_section({"node_execution": {}}) == ""
    assert read_shard_output(tmp_path, {}) == ""


def test_read_shard_output_for_shard(tmp_path: Path) -> None:
    (tmp_path / SHARD_OUTPUT_NAME).write_text('{"r": 1}', encoding="utf-8")
    assert read_shard_output(tmp_path, {"shard_index": 0}) == '{"r": 1}'
