"""Shard input/output contract shared by builtin executors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

SHARD_OUTPUT_NAME = "shard_output.json"


def shard_output_name(runtime: Mapping[str, object]) -> str:
    """Return the output filename for a shard run.

    Each shard gets its own ``shard_output-<index>.json`` so concurrent local
    shards sharing the job directory cannot clobber one another. Non-shard
    runs keep the legacy shared name (used only outside the shard flow).
    """
    shard_index = runtime.get("shard_index")
    if shard_index is None:
        return SHARD_OUTPUT_NAME
    return f"shard_output-{shard_index}.json"


def shard_prompt_section(runtime: Mapping[str, object]) -> str:
    """Render the prompt appendix for a shard run, or empty for a normal run."""
    if runtime.get("shard_index") is None:
        return ""
    return (
        "\nShard execution:\n"
        f"- Shard index: {runtime['shard_index']}\n"
        f"- Shard input (JSON): {json.dumps(runtime.get('shard_input'), ensure_ascii=False)}\n"
        f"- Write the shard result as JSON to {shard_output_name(runtime)} in the working directory.\n"
    )


def read_shard_output(job_dir: Path, runtime: Mapping[str, object]) -> str:
    """Read a shard capability's JSON output for the neutral result contract."""
    if runtime.get("shard_index") is None:
        return ""
    path = job_dir / shard_output_name(runtime)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    # Fallback for shard runs started before the per-shard filename existed.
    legacy = job_dir / SHARD_OUTPUT_NAME
    return legacy.read_text(encoding="utf-8") if legacy.is_file() else ""
