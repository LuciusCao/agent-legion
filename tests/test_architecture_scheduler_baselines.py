from pathlib import Path

from scripts.check_architecture import check_repository


def test_scheduler_import_baseline_rejects_unused_allowance(tmp_path: Path) -> None:
    path = tmp_path / "server/app/pipelines/scheduler.py"
    path.parent.mkdir(parents=True)
    path.write_text("from pathlib import Path\n", encoding="utf-8")
    config_path = tmp_path / "config/architecture-budgets.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '{"scheduler_import_baselines": '
        '{"server/app/pipelines/scheduler.py": ["server.app.pipelines.pi_runner"]}}',
        encoding="utf-8",
    )

    assert any("unused scheduler import baseline" in error for error in check_repository(tmp_path))
