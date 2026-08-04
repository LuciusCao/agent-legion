from scripts.architecture.repository import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance


def test_rejects_frontend_workflow_concurrency_contract(tmp_path):
    source = tmp_path / "frontend/src/types.ts"
    source.parent.mkdir(parents=True)
    source.write_text(
        "export type Workflow = { concurrency: number }\n"
        "export const read = (workflow: Workflow) => workflow.concurrency\n",
        encoding="utf-8",
    )
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any("WorkflowDefinition.concurrency attribute" in error for error in errors)
