from pathlib import Path

import pytest

from scripts.check_architecture import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _empty_budgets(path: Path) -> None:
    write_neutral_budget_governance(path)


class TestWorkspaceLegacyVideoBoundary:
    @pytest.mark.parametrize(
        "rel_path,source,expected_fragment",
        [
            (
                "server/app/routes/jobs.py",
                "from fastapi import APIRouter\n"
                "from server.app.pipeline.download import fetch_media\n"
                "router = APIRouter()\n",
                "download",
            ),
        ],
    )
    def test_rejects_legacy_video_imports(self, tmp_path, rel_path, source, expected_fragment):
        write(tmp_path / rel_path, source)
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any("legacy video" in error and expected_fragment in error for error in errors)

    def test_allows_generic_workflow_imports_in_workspace_services(self, tmp_path):
        write(
            tmp_path / "server/app/services/job_execution.py",
            "from server.app.workflows.definition import WorkflowDefinition\n"
            "from server.app.workflows.scheduler import downstream_nodes\n"
            "from server.app.workflows.execution_control import ancestor_closure\n"
            "class JobExecutionService:\n"
            "    pass\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert not any("legacy video" in error for error in errors)


class TestJobExecutionExecutorBoundary:
    @pytest.mark.parametrize(
        "rel_path,source",
        [
            (
                "server/app/services/job_execution.py",
                "from server.app.executors.code import CodeExecutor\n"
                "class JobExecutionService:\n"
                "    def run(self):\n"
                "        CodeExecutor(id='x', handlers={}).execute(None)\n",
            ),
            (
                "server/app/services/job_rerun.py",
                "from server.app.executors.registry import ExecutorRegistry\n"
                "class JobRerunService:\n"
                "    pass\n",
            ),
        ],
    )
    def test_rejects_direct_executor_use_in_job_service(self, tmp_path, rel_path, source):
        write(tmp_path / rel_path, source)
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any("direct Executor" in error for error in errors)

    def test_allows_executor_leases_in_job_services(self, tmp_path):
        write(
            tmp_path / "server/app/services/job_execution.py",
            "from server.app.executors.leases import ExecutorLeaseRepository\n"
            "class JobExecutionService:\n"
            "    pass\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert not any("direct Executor" in error for error in errors)


class TestFrontendJobTransportTypes:
    @pytest.mark.parametrize(
        "rel_path,source,expected_type",
        [
            (
                "frontend/src/types.ts",
                "export type JobSummaryResponse = { id: string }\n",
                "JobSummaryResponse",
            ),
            (
                "frontend/src/workspaceTypes.ts",
                "export type WorkspacePackageResponse = { results: any[] }\n",
                "WorkspacePackageResponse",
            ),
        ],
    )
    def test_rejects_handwritten_transport_types(self, tmp_path, rel_path, source, expected_type):
        write(tmp_path / rel_path, source)
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any("handwritten transport" in error and expected_type in error for error in errors)

    def test_accepts_derived_job_response_type(self, tmp_path):
        write(
            tmp_path / "frontend/src/types.ts",
            "import type { components } from './generated/api'\n"
            "type ApiSchemas = components['schemas']\n"
            "export type JobSummaryResponse = ApiSchemas['JobSummaryResponse']\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert not any("handwritten transport" in error for error in errors)


class TestSchemaMutationLocations:
    @pytest.mark.parametrize(
        "rel_path,source,expected_error",
        [
            (
                "server/app/services/job_queries.py",
                "class JobQueries:\n"
                "    def add_column(self, conn):\n"
                '        conn.execute("alter table jobs add column x text")\n',
                "schema mutation",
            ),
        ],
    )
    def test_rejects_schema_mutation_outside_migrations(
        self, tmp_path, rel_path, source, expected_error
    ):
        write(tmp_path / rel_path, source)
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any(expected_error in error for error in errors)

    @pytest.mark.parametrize(
        "rel_path,source",
        [
            (
                "server/app/db/migrations/v999_test.py",
                'def migrate(conn):\n    conn.execute("create table test (id text)")\n',
            ),
            (
                "server/app/db/schema.py",
                'SCHEMA_SQL = "create table if not exists videos (id text)"\n',
            ),
        ],
    )
    def test_allows_schema_mutation_in_allowed_locations(self, tmp_path, rel_path, source):
        write(tmp_path / rel_path, source)
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert not any("schema mutation" in error for error in errors)


def test_phase6_current_repository_has_no_errors():
    errors = check_repository(ROOT)
    phase6_errors = [
        error
        for error in errors
        if any(
            tag in error
            for tag in (
                "legacy video",
                "direct Executor",
                "DAG traversal",
                "filesystem deletion",
                "handwritten transport",
                "schema mutation",
            )
        )
    ]
    assert not phase6_errors, "Unexpected Phase 6 architecture errors:\n" + "\n".join(phase6_errors)
