from pathlib import Path

from scripts.check_architecture import check_repository

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _empty_budgets(path: Path) -> None:
    write(path / "config/architecture-budgets.json", '{"route_exemptions": [], "files": {}}')


class TestWorkspaceVideoHiveBoundary:
    def test_rejects_video_hive_phase_import_in_workspace_route(self, tmp_path):
        write(
            tmp_path / "server/app/routes/jobs.py",
            "from fastapi import APIRouter\n"
            "from server.app.pipeline.download import download_video\n"
            "router = APIRouter()\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any("Video Hive" in error and "download" in error for error in errors)

    def test_rejects_video_hive_service_import_in_workspace_service(self, tmp_path):
        write(
            tmp_path / "server/app/services/job_execution.py",
            "from server.app.services.video_actions import delete_video\n"
            "class JobExecutionService:\n"
            "    def run(self): delete_video('x')\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any("Video Hive" in error and "video_actions" in error for error in errors)

    def test_allows_generic_pipeline_imports_in_workspace_services(self, tmp_path):
        write(
            tmp_path / "server/app/services/job_execution.py",
            "from server.app.pipelines.definition import PipelineDefinition\n"
            "from server.app.pipelines.scheduler import downstream_nodes\n"
            "from server.app.pipelines.execution_control import ancestor_closure\n"
            "class JobExecutionService:\n"
            "    pass\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert not any("Video Hive" in error for error in errors)


class TestJobExecutionExecutorBoundary:
    def test_rejects_direct_executor_invocation_in_job_service(self, tmp_path):
        write(
            tmp_path / "server/app/services/job_execution.py",
            "from server.app.executors.local import LocalExecutor\n"
            "class JobExecutionService:\n"
            "    def run(self):\n"
            "        LocalExecutor(id='x', handlers={}).execute(None)\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any("direct Executor" in error for error in errors)

    def test_rejects_executor_registry_import_in_job_service(self, tmp_path):
        write(
            tmp_path / "server/app/services/job_rerun.py",
            "from server.app.executors.registry import ExecutorRegistry\n"
            "class JobRerunService:\n"
            "    pass\n",
        )
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


class TestRouteDagAndDeletionBoundary:
    def test_rejects_dag_traversal_in_workspace_route(self, tmp_path):
        write(
            tmp_path / "server/app/routes/jobs.py",
            "from fastapi import APIRouter\n"
            "from server.app.pipelines.scheduler import downstream_nodes\n"
            "router = APIRouter()\n"
            "@router.post('/x')\n"
            "def x():\n"
            "    return downstream_nodes(None, 'a')\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any("DAG traversal" in error for error in errors)

    def test_rejects_filesystem_deletion_in_workspace_route(self, tmp_path):
        write(
            tmp_path / "server/app/routes/jobs.py",
            "import shutil\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.delete('/x')\n"
            "def x():\n"
            "    shutil.rmtree('/tmp/x')\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any("filesystem deletion" in error for error in errors)

    def test_allows_deletion_in_services(self, tmp_path):
        write(
            tmp_path / "server/app/services/job_deletion.py",
            "import shutil\n"
            "from pathlib import Path\n"
            "class JobDeletionService:\n"
            "    def delete(self):\n"
            "        shutil.rmtree(Path('/tmp/x'))\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert not any("filesystem deletion" in error for error in errors)


class TestFrontendJobTransportTypes:
    def test_rejects_handwritten_job_summary_response(self, tmp_path):
        write(
            tmp_path / "frontend/src/types.ts",
            "export type JobSummaryResponse = { id: string }\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any(
            "handwritten transport" in error and "JobSummaryResponse" in error for error in errors
        )

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

    def test_rejects_handwritten_workspace_package_response(self, tmp_path):
        write(
            tmp_path / "frontend/src/workspaceTypes.ts",
            "export type WorkspacePackageResponse = { results: any[] }\n",
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any(
            "handwritten transport" in error and "WorkspacePackageResponse" in error
            for error in errors
        )


class TestSchemaMutationLocations:
    def test_rejects_schema_mutation_outside_migrations(self, tmp_path):
        write(
            tmp_path / "server/app/services/job_queries.py",
            "class JobQueries:\n"
            "    def add_column(self, conn):\n"
            '        conn.execute("alter table jobs add column x text")\n',
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert any("schema mutation" in error for error in errors)

    def test_allows_schema_mutation_in_migrations(self, tmp_path):
        write(
            tmp_path / "server/app/db/migrations/v999_test.py",
            'def migrate(conn):\n    conn.execute("create table test (id text)")\n',
        )
        _empty_budgets(tmp_path)

        errors = check_repository(tmp_path)

        assert not any("schema mutation" in error for error in errors)

    def test_allows_schema_mutation_in_schema_module(self, tmp_path):
        write(
            tmp_path / "server/app/db/schema.py",
            'SCHEMA_SQL = "create table if not exists videos (id text)"\n',
        )
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
                "Video Hive",
                "direct Executor",
                "DAG traversal",
                "filesystem deletion",
                "handwritten transport",
                "schema mutation",
            )
        )
    ]
    assert not phase6_errors, "Unexpected Phase 6 architecture errors:\n" + "\n".join(phase6_errors)
