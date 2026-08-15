"""Demo workflow terminal node: aggregate upstream artifacts into a publish payload.

Last node of the ``education_video_problems_generation`` example workflow
(capability ``publish_content``). It reads the upstream artifacts
(knowledge point, script, both reviews, exercises), writes a single
``publish_payload.json``, and logs the simulated content-library insert —
deliberately **no network requests**: a fresh open-source checkout has no
external content service, so "publish" is demonstrated as a local payload.

Pure stdlib + node SDK.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace_libs.node_sdk import NodeContext

_SCRIPT_INPUT = "script.md"
_JSON_INPUTS = (
    "knowledge_point.json",
    "script_review.json",
    "exercises.json",
    "exercises_review.json",
)


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    log = ctx.logger
    ctx.checkpoint()

    knowledge_point = ctx.artifacts.read_json_object("knowledge_point.json")["knowledge_point"]
    script_text = ctx.artifacts.read_text(_SCRIPT_INPUT)
    script_review = ctx.artifacts.read_json_object("script_review.json")
    exercises_payload = ctx.artifacts.read_json_object("exercises.json")
    exercises_review = ctx.artifacts.read_json_object("exercises_review.json")
    exercises = exercises_payload.get("exercises") or []

    payload = {
        "job_id": str(job.get("id", "")),
        "workflow": ctx.workflow_manifest(default_key="education_video_problems_generation"),
        "knowledge_point": knowledge_point,
        "script": {
            "artifact": _SCRIPT_INPUT,
            "content": script_text,
            "chars": len(script_text),
        },
        "script_review": script_review,
        "exercises": exercises,
        "exercises_review": exercises_review,
        # Marker for downstream consumers and UI: this payload was produced by
        # the demo's simulated publish, not by a real content service.
        "simulated": True,
    }
    ctx.checkpoint()
    out_path = ctx.artifacts.write_json("publish_payload.json", payload)

    log.info("example_publish: 模拟入库（demo 节点，不发任何网络请求）")
    log.info(
        "  [mock-db] INSERT INTO teaching_contents (knowledge_point, script_chars, "
        "exercise_count) VALUES (%r, %d, %d)",
        knowledge_point.get("title", ""),
        len(script_text),
        len(exercises),
    )
    log.info("  [mock-db] payload artifact: %s", out_path.name)
