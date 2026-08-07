from pathlib import Path

import pytest

from server.app.main import create_app
from server.app.settings import load_settings

pytestmark = pytest.mark.full_gate


def test_repository_split_configuration_builds_application(tmp_path: Path, monkeypatch):
    for key in (
        "CMS_BASE_URL",
        "CMS_TOKEN",
        "CMS_APP_ID",
        "CMS_NONCE",
        "CMS_SECRET",
        "CMS_TOKEN_URL",
        "BASECMS_BASE_URL",
        "BASECMS_TOKEN",
        "BASECMS_APP_ID",
        "BASECMS_NONCE",
        "BASECMS_SECRET",
        "BASECMS_TOKEN_URL",
        "AGENT_LEGION_CMS_TOKEN",
        "AGENT_LEGION_CMS_TOKEN_GEN_SECRET",
    ):
        monkeypatch.setenv(key, "")
    settings = load_settings(data_dir=tmp_path / "settings-data")
    assert settings.executor_runtime.workflows.enabled is True
    assert settings.executor_definitions
    app = create_app(data_dir=tmp_path / "app-data", start_worker=False)
    assert app.state.settings.config["data_dir"] == "data"
    assert app.state.executor_registry is not None
