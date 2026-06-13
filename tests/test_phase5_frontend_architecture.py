from scripts.architecture.repository import check_repository


def test_rejects_frontend_pipeline_concurrency_contract(tmp_path):
    source = tmp_path / "frontend/src/types.ts"
    source.parent.mkdir(parents=True)
    source.write_text(
        "export type Pipeline = { concurrency: number }\n"
        "export const read = (pipeline: Pipeline) => pipeline.concurrency\n",
        encoding="utf-8",
    )
    config = tmp_path / "config/architecture-budgets.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"route_exemptions": [], "files": {}}', encoding="utf-8")

    errors = check_repository(tmp_path)

    assert any("PipelineDefinition.concurrency attribute" in error for error in errors)
