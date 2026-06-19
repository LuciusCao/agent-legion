import ast
import importlib
from pathlib import Path

from scripts.check_architecture import check_repository


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_budgets(root: Path) -> None:
    write(root / "config/architecture/architecture-budgets.json", '{"files": {}}')


def test_services_do_not_import_fastapi_regardless_of_filename(tmp_path):
    write(
        tmp_path / "server/app/services/catalog.py",
        "from fastapi import HTTPException\n",
    )
    write_budgets(tmp_path)
    errors = check_repository(tmp_path)
    assert any("service boundary forbids import fastapi" in error for error in errors)


def test_services_do_not_import_worker_entrypoints(tmp_path):
    write(
        tmp_path / "server/app/services/manual_run.py",
        "from server.app.worker import process_video_once\nfrom ..worker import process_video_once\nfrom .. import worker\n",
    )
    write(
        tmp_path / "server/app/services/feature/manual_run.py",
        "from ..worker import process_video_once\nfrom ...worker import process_video_once\n",
    )
    write_budgets(tmp_path)
    report = "\n".join(check_repository(tmp_path))
    worker_error = "service boundary forbids import server.app.worker"
    for line in (1, 2, 3):
        assert f"services/manual_run.py:{line}: {worker_error}" in report
    assert "feature/manual_run.py:1: service boundary" not in report
    assert f"feature/manual_run.py:2: {worker_error}" in report


def test_repository_preserves_service_diagnostic_order(tmp_path):
    write(
        tmp_path / "server/app/services/example.py",
        "from ..worker import process_video_once\nimport fastapi\nimport server.app.worker\n",
    )
    write_budgets(tmp_path)

    assert check_repository(tmp_path) == [
        "server/app/services/example.py:1: service boundary forbids import server.app.worker",
        "server/app/services/example.py:2: service boundary forbids import fastapi",
        "server/app/services/example.py:3: service boundary forbids import server.app.worker",
    ]


def test_focused_service_analyzer_matches_repository_diagnostics():
    service_boundaries = importlib.import_module("scripts.architecture.service_boundaries")
    source = "from ..worker import process_video_once\nimport fastapi\nimport server.app.worker\n"

    assert service_boundaries.check_service_import_boundaries(
        "server/app/services/example.py", ast.parse(source)
    ) == [
        "server/app/services/example.py:1: service boundary forbids import server.app.worker",
        "server/app/services/example.py:2: service boundary forbids import fastapi",
        "server/app/services/example.py:3: service boundary forbids import server.app.worker",
    ]
    assert (
        service_boundaries.check_service_import_boundaries(
            "server/app/routes/example.py", ast.parse("import fastapi\n")
        )
        == []
    )
