from scripts.architecture.repository import check_repository


def test_rejects_frontend_workflow_concurrency_contract(tmp_path):
    source = tmp_path / "frontend/src/types.ts"
    source.parent.mkdir(parents=True)
    source.write_text(
        "export type Workflow = { concurrency: number }\n"
        "export const read = (workflow: Workflow) => workflow.concurrency\n",
        encoding="utf-8",
    )
    config = tmp_path / "config/architecture-budgets.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"route_exemptions": [], "files": {}}', encoding="utf-8")

    errors = check_repository(tmp_path)

    assert any("WorkflowDefinition.concurrency attribute" in error for error in errors)
