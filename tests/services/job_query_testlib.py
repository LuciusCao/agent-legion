"""tests/services 下 JobQueryService 测试族的共享 scaffolding。

test modules 之间禁止互相 import（rename-isolated 边界，见
test_test_modules_do_not_import_each_other），共享的 job 播种 helper
集中在这里（先例：materials_testlib.py）。
"""

from typing import Any

from tests.helpers import publish_builtin_revision


def create_question_job(job_db, source_id: str) -> dict[str, Any]:
    workspace = job_db.get_workspace("default") or job_db.create_workspace("default")
    # Jobs created without an intake snapshot resolve their definition from
    # the workspace's active revision (schema v50), so publish it.
    publish_builtin_revision(job_db, workspace["id"])
    batch = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": [source_id]},
        workspace_id=workspace["id"],
    )
    job: dict[str, Any] = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id=source_id,
        run_id=batch["id"],
        title=f"Question {source_id}",
        node_keys=["question_understanding", "assemble_package"],
        workspace_id=workspace["id"],
    )
    return job
