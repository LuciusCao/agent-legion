"""question_comprehension_info node: assemble the final comprehension payload.

Validates the reviewed agent artifacts (key info, possible errors,
difficulty) against the parsed question, then writes
``comprehension_info.json`` and the job ``manifest.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace_libs.comprehension_common import (
    _assert_artifact_question_id,
    _single_parsed_question,
)
from workspace_libs.comprehension_contract import assert_comprehension_lists_contract
from workspace_libs.node_sdk import NodeContext


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    log = ctx.logger
    source_id = str(job["source_id"])
    log.info("assemble_comprehension_info: source_id=%s", source_id)
    question = _single_parsed_question(job_dir, source_id, "questions_parsed_lean.json")
    key_info = ctx.artifacts.read_json_object("key_info_reviewed.json")
    possible_errors = ctx.artifacts.read_json_object("possible_errors_reviewed.json")
    difficulty = ctx.artifacts.read_json_object("comprehension_difficulty.json")

    log.info("  validating input artifacts")
    for name, content in (
        ("key_info_reviewed.json", key_info),
        ("possible_errors_reviewed.json", possible_errors),
        ("comprehension_difficulty.json", difficulty),
    ):
        ctx.checkpoint()
        _assert_artifact_question_id(name, content, source_id)

    fingerprint = question.get("fingerprint")
    if fingerprint is not None and not isinstance(fingerprint, str):
        raise ValueError("fingerprint must be a string or null")

    key_info_list = key_info.get("key_info_list", [])
    possible_error_list = possible_errors.get("possible_error_list", [])
    ctx.checkpoint()
    assert_comprehension_lists_contract(key_info_list, possible_error_list)

    comprehension_data = {
        "fingerprint": fingerprint,
        "comprehension_difficulty": difficulty.get("comprehension_difficulty"),
        "key_info_list": key_info_list,
        "possible_error_list": possible_error_list,
    }
    payload = {
        "question_id": source_id,
        "fingerprint": fingerprint,
        "fingerprint_source": question.get("fingerprint_source", "missing"),
        "fingerprint_missing": fingerprint is None,
        "schema_version": "v1",
        "comprehension_data": comprehension_data,
    }
    manifest = {
        "question_id": source_id,
        "workflow_key": job.get("workflow_key", "question_comprehension_info"),
        "workflow": ctx.workflow_manifest("question_comprehension_info"),
        "source_type": job.get("source_type", "question"),
        "title": job.get("title", ""),
        "fingerprint": fingerprint,
        "fingerprint_missing": fingerprint is None,
        "artifacts": {
            "comprehension_info.json": {"present": True},
        },
        "skill_versions": ctx.skill_versions,
    }

    ctx.artifacts.write_json("comprehension_info.json", payload)
    ctx.artifacts.write_json("manifest.json", manifest)
    log.info("  wrote comprehension_info.json and manifest.json")
