"""question_comprehension_info node: finalize a non-uploadable question.

Runs when the classify node marked the question ineligible: writes the final
``manifest.json`` with ``uploadable: False`` and the skip reason, so the job
completes without generating comprehension info.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace_libs.comprehension_common import (
    _assert_artifact_question_id,
    _single_parsed_question,
)
from workspace_libs.node_sdk import NodeContext


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    source_id = str(job["source_id"])
    question = _single_parsed_question(job_dir, source_id)
    eligibility = ctx.artifacts.read_json_object("comprehension_eligibility.json")
    _assert_artifact_question_id("comprehension_eligibility.json", eligibility, source_id)
    fingerprint = question.get("fingerprint")
    manifest = {
        "question_id": source_id,
        "workflow_key": job.get("workflow_key", "question_comprehension_info"),
        "source_type": job.get("source_type", "question"),
        "title": job.get("title", ""),
        "fingerprint": fingerprint,
        "fingerprint_missing": fingerprint is None,
        "uploadable": False,
        "outcome": "non_uploadable",
        "skip_reason_code": eligibility.get("reason_code", ""),
        "skip_reason": eligibility.get("reason", ""),
        "artifacts": {
            "comprehension_info.json": {"present": False},
        },
        "skill_versions": ctx.skill_versions,
    }
    ctx.artifacts.write_json("manifest.json", manifest)
