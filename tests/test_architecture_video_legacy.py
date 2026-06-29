from pathlib import Path

import pytest

from scripts.check_architecture import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _empty_budgets(path: Path) -> None:
    write_neutral_budget_governance(path)


def run_architecture_check_for_source(rel_path: str, source: str, tmp_path: Path) -> list[str]:
    write(tmp_path / rel_path, source)
    _empty_budgets(tmp_path)
    return check_repository(tmp_path)


class TestVideoCapabilitiesBoundary:
    def test_video_capabilities_do_not_import_orchestration_tables(self, tmp_path: Path):
        errors = run_architecture_check_for_source(
            "server/app/video_capabilities/local.py",
            "from server.app.db import Database\n",
            tmp_path,
        )
        assert any("video_capabilities" in error for error in errors)

    @pytest.mark.parametrize(
        "rel_path,source,expected_fragment",
        [
            (
                "server/app/video_capabilities/local.py",
                "from server.app.jobs import JobQueries\n",
                "video_capabilities",
            ),
            (
                "server/app/video_capabilities/local.py",
                "from server.app.routes import create_router\n",
                "video_capabilities",
            ),
            (
                "server/app/video_capabilities/local.py",
                "from server.app.worker import Worker\n",
                "video_capabilities",
            ),
            (
                "server/app/video_capabilities/local.py",
                "from server.app.worker_thread import WorkerThread\n",
                "video_capabilities",
            ),
        ],
    )
    def test_rejects_orchestration_imports(
        self, tmp_path: Path, rel_path: str, source: str, expected_fragment: str
    ):
        errors = run_architecture_check_for_source(rel_path, source, tmp_path)
        assert any(expected_fragment in error for error in errors)

    def test_allows_internal_video_capability_imports(self, tmp_path: Path):
        errors = run_architecture_check_for_source(
            "server/app/video_capabilities/local.py",
            "from server.app.video_capabilities.contracts import VideoCapability\n"
            "from server.app.pipeline.common import parse_srt_file\n",
            tmp_path,
        )
        assert not any("video_capabilities" in error for error in errors)


class TestLegacyVideoRouteImports:
    def test_workspace_video_code_does_not_import_legacy_video_routes(self, tmp_path: Path):
        errors = run_architecture_check_for_source(
            "server/app/services/new_video.py",
            "from server.app.routes.videos import create_videos_router\n",
            tmp_path,
        )
        assert any("legacy video" in error.lower() for error in errors)

    def test_rejects_relative_import_of_legacy_videos_router(self, tmp_path: Path):
        errors = run_architecture_check_for_source(
            "server/app/services/new_video.py",
            "from ..routes.videos import create_videos_router\n",
            tmp_path,
        )
        assert any("legacy video" in error.lower() for error in errors)

    def test_allows_workspace_video_jobs_router(self, tmp_path: Path):
        errors = run_architecture_check_for_source(
            "server/app/services/new_video.py",
            "from server.app.routes.video_jobs import create_video_jobs_router\n",
            tmp_path,
        )
        assert not any("legacy video" in error.lower() for error in errors)


class TestWorkspacePipelinePhaseImports:
    def test_rejects_pipeline_phases_import_in_workspace_service(self, tmp_path: Path):
        errors = run_architecture_check_for_source(
            "server/app/services/job_execution.py",
            "from server.app.pipeline.phases import phase_sequence\n",
            tmp_path,
        )
        assert any("Video Hive" in error or "legacy pipeline phase" in error for error in errors)

    def test_allows_pipeline_package_import_in_workspace_service(self, tmp_path: Path):
        errors = run_architecture_check_for_source(
            "server/app/services/job_execution.py",
            "from server.app.pipeline.package import build_package\n",
            tmp_path,
        )
        assert not any(
            "Video Hive" in error or "legacy pipeline phase" in error for error in errors
        )


def test_video_legacy_current_repository_has_no_errors():
    errors = check_repository(ROOT)
    video_legacy_errors = [
        error
        for error in errors
        if any(
            tag in error.lower()
            for tag in (
                "video_capabilities",
                "legacy video",
                "legacy pipeline phase",
            )
        )
    ]
    assert not video_legacy_errors, "Unexpected video legacy architecture errors:\n" + "\n".join(
        video_legacy_errors
    )
