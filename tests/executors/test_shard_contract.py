from __future__ import annotations

from pathlib import Path

from server.app.executors._shard_contract import (
    SHARD_OUTPUT_NAME,
    read_shard_output,
    shard_output_name,
    shard_prompt_section,
)


def test_shard_prompt_section_renders_index_input_and_output_contract() -> None:
    section = shard_prompt_section({"shard_index": 2, "shard_input": {"q": 5}})
    assert "Shard index: 2" in section
    assert '"q": 5' in section
    assert "shard_output-2.json" in section


def test_shard_output_name_is_per_shard() -> None:
    assert shard_output_name({"shard_index": 0}) == "shard_output-0.json"
    assert shard_output_name({"shard_index": 3}) == "shard_output-3.json"
    assert shard_output_name({"node_execution": {}}) == SHARD_OUTPUT_NAME


def test_non_shard_has_no_prompt_or_output(tmp_path: Path) -> None:
    (tmp_path / SHARD_OUTPUT_NAME).write_text('{"r": 1}', encoding="utf-8")
    assert shard_prompt_section({"node_execution": {}}) == ""
    assert read_shard_output(tmp_path, {}) == ""


def test_read_shard_output_uses_per_shard_files(tmp_path: Path) -> None:
    """Concurrent shards in one job dir read their own output file."""
    (tmp_path / "shard_output-0.json").write_text('{"a": 0}', encoding="utf-8")
    (tmp_path / "shard_output-1.json").write_text('{"a": 1}', encoding="utf-8")
    assert read_shard_output(tmp_path, {"shard_index": 0}) == '{"a": 0}'
    assert read_shard_output(tmp_path, {"shard_index": 1}) == '{"a": 1}'


def test_read_shard_output_per_shard_file_wins_over_legacy(tmp_path: Path) -> None:
    (tmp_path / SHARD_OUTPUT_NAME).write_text('{"legacy": true}', encoding="utf-8")
    (tmp_path / "shard_output-0.json").write_text('{"new": true}', encoding="utf-8")
    assert read_shard_output(tmp_path, {"shard_index": 0}) == '{"new": true}'


def test_read_shard_output_falls_back_to_legacy_name(tmp_path: Path) -> None:
    """Shard runs started before the per-shard filename still resolve."""
    (tmp_path / SHARD_OUTPUT_NAME).write_text('{"r": 1}', encoding="utf-8")
    assert read_shard_output(tmp_path, {"shard_index": 0}) == '{"r": 1}'


def test_read_shard_output_missing_file(tmp_path: Path) -> None:
    assert read_shard_output(tmp_path, {"shard_index": 0}) == ""
