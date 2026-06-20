from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from server.app.configuration.loader import ConfigurationLoadError

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrate-config-layout.py"


@pytest.fixture(scope="session")
def migration_module() -> Any:
    name = "migrate_config_layout"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def split_config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text("data_dir: data\nserver: {port: 8000}\n", encoding="utf-8")
    (config_dir / "video_hive.yaml").write_text("cms: {token: ''}\n", encoding="utf-8")
    (config_dir / "workflow.yaml").write_text("executors: {}\n", encoding="utf-8")
    return config_dir


@pytest.fixture
def legacy_config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "workflow.yaml").write_text(
        "data_dir: data\ncms: {token: ''}\nexecutors: {}\n", encoding="utf-8"
    )
    return config_dir


class TestCheckLayout:
    def test_check_classifies_legacy_keys(self, migration_module: Any, tmp_path: Path) -> None:
        (tmp_path / "workflow.yaml").write_text(
            "data_dir: data\ncms: {token: ''}\nexecutors: {}\n", encoding="utf-8"
        )
        report = migration_module.check_layout(tmp_path)
        assert report.status == "legacy"
        assert report.keys_by_file == {
            "app.yaml": ("data_dir",),
            "video_hive.yaml": ("cms",),
            "workflow.yaml": ("executors",),
        }

    def test_check_rejects_unknown_key(self, migration_module: Any, tmp_path: Path) -> None:
        (tmp_path / "workflow.yaml").write_text("mystery: true\n", encoding="utf-8")
        with pytest.raises(ConfigurationLoadError, match="mystery"):
            migration_module.check_layout(tmp_path)

    def test_check_accepts_complete_split_layout(
        self, migration_module: Any, split_config_dir: Path
    ) -> None:
        report = migration_module.check_layout(split_config_dir)
        assert report.status == "split"


class TestMainCheck:
    def test_main_check_returns_zero_for_legacy(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        (tmp_path / "workflow.yaml").write_text("data_dir: data\n", encoding="utf-8")
        assert migration_module.main(["--check", "--config-dir", str(tmp_path)]) == 0

    def test_main_check_returns_zero_for_split(
        self, migration_module: Any, split_config_dir: Path
    ) -> None:
        assert migration_module.main(["--check", "--config-dir", str(split_config_dir)]) == 0

    def test_main_check_returns_one_for_partial(
        self, migration_module: Any, tmp_path: Path, capsys: Any
    ) -> None:
        (tmp_path / "app.yaml").write_text("{}", encoding="utf-8")
        assert migration_module.main(["--check", "--config-dir", str(tmp_path)]) == 1
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err
        assert "partial" in captured.err.lower()

    def test_main_check_returns_one_for_invalid_yaml(
        self, migration_module: Any, tmp_path: Path, capsys: Any
    ) -> None:
        (tmp_path / "workflow.yaml").write_text("[", encoding="utf-8")
        assert migration_module.main(["--check", "--config-dir", str(tmp_path)]) == 1
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err

    def test_main_check_returns_one_for_unknown_key(
        self, migration_module: Any, tmp_path: Path, capsys: Any
    ) -> None:
        (tmp_path / "workflow.yaml").write_text("mystery: true\n", encoding="utf-8")
        assert migration_module.main(["--check", "--config-dir", str(tmp_path)]) == 1
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err
        assert "mystery" in captured.err

    def test_main_check_does_not_print_values(
        self, migration_module: Any, tmp_path: Path, capsys: Any
    ) -> None:
        (tmp_path / "workflow.yaml").write_text(
            "data_dir: secret_value\ncms: {token: 'secret_token'}\n", encoding="utf-8"
        )
        migration_module.main(["--check", "--config-dir", str(tmp_path)])
        captured = capsys.readouterr()
        assert "secret_value" not in captured.out
        assert "secret_token" not in captured.out
        assert "secret_value" not in captured.err
        assert "secret_token" not in captured.err


class TestApplyLayout:
    def test_apply_creates_timestamped_backup_with_mode_bits(
        self, migration_module: Any, legacy_config_dir: Path
    ) -> None:
        source = legacy_config_dir / "workflow.yaml"
        source.chmod(0o640)
        fixed_now = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)

        migration_module.apply_layout(legacy_config_dir, now=lambda: fixed_now)

        backups = sorted(legacy_config_dir.glob("workflow.yaml.bak-*"))
        assert len(backups) == 1
        assert backups[0].name == "workflow.yaml.bak-20240102030405"
        assert backups[0].stat().st_mode & 0o777 == 0o640

    def test_apply_writes_three_owned_files(
        self, migration_module: Any, legacy_config_dir: Path
    ) -> None:
        migration_module.apply_layout(legacy_config_dir)

        app_yaml = migration_module.load_yaml_mapping(legacy_config_dir / "app.yaml")
        video_hive_yaml = migration_module.load_yaml_mapping(legacy_config_dir / "video_hive.yaml")
        workflow_yaml = migration_module.load_yaml_mapping(legacy_config_dir / "workflow.yaml")

        assert set(app_yaml) == {"data_dir"}
        assert set(video_hive_yaml) == {"cms"}
        assert set(workflow_yaml) == {"executors"}

    def test_apply_preserves_merged_mapping(
        self, migration_module: Any, legacy_config_dir: Path
    ) -> None:
        source = migration_module.load_yaml_mapping(legacy_config_dir / "workflow.yaml")
        migration_module.apply_layout(legacy_config_dir)

        sections = [
            (
                legacy_config_dir / "app.yaml",
                migration_module.load_yaml_mapping(legacy_config_dir / "app.yaml"),
            ),
            (
                legacy_config_dir / "video_hive.yaml",
                migration_module.load_yaml_mapping(legacy_config_dir / "video_hive.yaml"),
            ),
            (
                legacy_config_dir / "workflow.yaml",
                migration_module.load_yaml_mapping(legacy_config_dir / "workflow.yaml"),
            ),
        ]
        assert migration_module.merge_config_sections(sections) == source

    def test_apply_is_noop_on_split_layout(
        self, migration_module: Any, split_config_dir: Path
    ) -> None:
        original_app = (split_config_dir / "app.yaml").read_text(encoding="utf-8")
        migration_module.apply_layout(split_config_dir)

        assert sorted(split_config_dir.glob("workflow.yaml.bak-*")) == []
        assert (split_config_dir / "app.yaml").read_text(encoding="utf-8") == original_app

    def test_apply_rejects_partial_layout_with_no_backup(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "app.yaml").write_text("data_dir: data\n", encoding="utf-8")

        with pytest.raises(ConfigurationLoadError, match="partial"):
            migration_module.apply_layout(config_dir)

        assert sorted(config_dir.glob("*.bak-*")) == []

    def test_apply_resumes_partial_replacement_from_backup(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup = config_dir / "workflow.yaml.bak-20240102030405"
        backup.write_text("data_dir: data\ncms: {token: ''}\nexecutors: {}\n", encoding="utf-8")
        (config_dir / "app.yaml").write_text("data_dir: data\n", encoding="utf-8")
        (config_dir / "video_hive.yaml").write_text("cms: {token: ''}\n", encoding="utf-8")

        migration_module.apply_layout(config_dir)

        workflow_yaml = migration_module.load_yaml_mapping(config_dir / "workflow.yaml")
        assert set(workflow_yaml) == {"executors"}

    def test_apply_refuses_recovery_when_slice_differs(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup = config_dir / "workflow.yaml.bak-20240102030405"
        backup.write_text("data_dir: data\ncms: {token: ''}\nexecutors: {}\n", encoding="utf-8")
        (config_dir / "app.yaml").write_text("data_dir: other\n", encoding="utf-8")
        (config_dir / "video_hive.yaml").write_text("cms: {token: ''}\n", encoding="utf-8")

        with pytest.raises(ConfigurationLoadError, match="rollback"):
            migration_module.apply_layout(config_dir)

    def test_apply_refuses_recovery_when_file_contains_unowned_key(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup = config_dir / "workflow.yaml.bak-20240102030405"
        backup.write_text("data_dir: data\ncms: {token: ''}\nexecutors: {}\n", encoding="utf-8")
        # app.yaml contains a key that does not belong to app.yaml. Recovery must not
        # silently drop it by slicing; it should require a rollback instead.
        (config_dir / "app.yaml").write_text("new_setting: keep-me\n", encoding="utf-8")

        with pytest.raises(ConfigurationLoadError, match="rollback"):
            migration_module.apply_layout(config_dir)

        # The user's extra key must survive the failed recovery attempt.
        assert (config_dir / "app.yaml").read_text(encoding="utf-8") == "new_setting: keep-me\n"

    def test_apply_recovers_when_workflow_yaml_still_has_legacy_content(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup = config_dir / "workflow.yaml.bak-20240102030405"
        backup.write_text("data_dir: data\ncms: {token: ''}\nexecutors: {}\n", encoding="utf-8")
        (config_dir / "app.yaml").write_text("data_dir: data\n", encoding="utf-8")
        (config_dir / "video_hive.yaml").write_text("cms: {token: ''}\n", encoding="utf-8")
        # workflow.yaml still contains the full legacy mapping, not just the slice.
        (config_dir / "workflow.yaml").write_text(
            "data_dir: data\ncms: {token: ''}\nexecutors: {}\n", encoding="utf-8"
        )

        migration_module.apply_layout(config_dir)

        workflow_yaml = migration_module.load_yaml_mapping(config_dir / "workflow.yaml")
        assert set(workflow_yaml) == {"executors"}

    def test_apply_recovers_from_inconsistent_split_layout(
        self, migration_module: Any, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup = config_dir / "workflow.yaml.bak-20240102030405"
        backup.write_text("data_dir: data\ncms: {token: ''}\nexecutors: {}\n", encoding="utf-8")
        # app.yaml and video_hive.yaml are valid split slices, but workflow.yaml contains
        # unowned legacy keys, making the detected layout an invalid split.
        (config_dir / "app.yaml").write_text("data_dir: data\n", encoding="utf-8")
        (config_dir / "video_hive.yaml").write_text("cms: {token: ''}\n", encoding="utf-8")
        (config_dir / "workflow.yaml").write_text(
            "data_dir: data\ncms: {token: ''}\nexecutors: {}\n", encoding="utf-8"
        )

        migration_module.apply_layout(config_dir)

        for name, expected_keys in {
            "app.yaml": {"data_dir"},
            "video_hive.yaml": {"cms"},
            "workflow.yaml": {"executors"},
        }.items():
            mapping = migration_module.load_yaml_mapping(config_dir / name)
            assert set(mapping) == expected_keys

    def test_apply_leaves_destinations_unchanged_on_validation_failure(
        self,
        migration_module: Any,
        legacy_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original_text = (legacy_config_dir / "workflow.yaml").read_text(encoding="utf-8")
        original_write_staged = migration_module._write_staged
        call_count = 0

        def failing_write_staged(path: Path, mapping: Any, mode: int) -> Path:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ConfigurationLoadError("forced validation failure")
            return original_write_staged(path, mapping, mode)  # type: ignore[no-any-return]

        monkeypatch.setattr(migration_module, "_write_staged", failing_write_staged)

        with pytest.raises(ConfigurationLoadError, match="forced validation failure"):
            migration_module.apply_layout(legacy_config_dir)

        assert (legacy_config_dir / "workflow.yaml").read_text(encoding="utf-8") == original_text
        assert sorted(legacy_config_dir.glob("*.bak-*")) == []
        assert not (legacy_config_dir / "app.yaml").exists()
        assert not (legacy_config_dir / "video_hive.yaml").exists()

    def test_apply_calls_before_replace_before_each_replace(
        self, migration_module: Any, legacy_config_dir: Path
    ) -> None:
        calls: list[str] = []

        def before_replace(path: Path) -> None:
            calls.append(path.name)

        migration_module.apply_layout(legacy_config_dir, before_replace=before_replace)

        assert calls == ["app.yaml", "video_hive.yaml", "workflow.yaml"]

    def test_apply_warns_about_comment_and_formatting_loss(
        self, migration_module: Any, legacy_config_dir: Path, capsys: Any
    ) -> None:
        report = migration_module.apply_layout(legacy_config_dir)
        rendered = report.render()
        assert "WARNING:" in rendered
        assert "comments" in rendered.lower()
        assert "formatting" in rendered.lower()

        captured = capsys.readouterr()
        assert "WARNING:" in captured.err
        assert "comments" in captured.err.lower()
        assert "formatting" in captured.err.lower()

    def test_apply_warning_printed_before_replacement(
        self, migration_module: Any, legacy_config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io

        captured = io.StringIO()
        monkeypatch.setattr("sys.stderr", captured)
        warning_seen_before_replace = False

        def before_replace(path: Path) -> None:
            nonlocal warning_seen_before_replace
            if not warning_seen_before_replace:
                warning_seen_before_replace = "WARNING:" in captured.getvalue()

        migration_module.apply_layout(legacy_config_dir, before_replace=before_replace)
        assert warning_seen_before_replace

    def test_apply_no_op_split_does_not_warn(
        self, migration_module: Any, split_config_dir: Path
    ) -> None:
        report = migration_module.apply_layout(split_config_dir)
        assert "WARNING" not in report.render()
