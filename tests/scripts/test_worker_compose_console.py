"""Compose must not advertise listener bindings as browser destinations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from worker.console_url import CONSOLE_URL_ENV, registration_config

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compose() -> str:
    executable = shutil.which("docker")
    if executable is None:
        pytest.skip("Docker Compose is required for interpolation regression")
    probe = subprocess.run([executable, "compose", "version"], capture_output=True, timeout=20)
    if probe.returncode:
        pytest.skip("Docker Compose plugin is unavailable")
    return executable


@pytest.mark.parametrize(
    "filename", ["compose.host.yaml", "compose.worker.yaml", "compose.worker.standalone.yaml"]
)
@pytest.mark.parametrize("bind", ["0.0.0.0", "::", "10.0.0.8"])
@pytest.mark.parametrize("override", [None, "", "https://worker.example/proxy?site=office"])
def test_compose_requires_browser_address(compose, tmp_path, filename, bind, override):
    source = yaml.safe_load((ROOT / "deploy" / filename).read_text())
    expression = next(
        service["environment"][CONSOLE_URL_ENV]
        for service in source["services"].values()
        if CONSOLE_URL_ENV in service.get("environment", {})
    )
    # Interpolate the real deployment expression with Compose, without loading
    # deployment secrets, requiring a daemon, or starting any containers.
    config = tmp_path / "compose.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "worker": {
                        "image": "example/worker:test",
                        "environment": {CONSOLE_URL_ENV: expression},
                    }
                }
            }
        )
    )
    environ = dict(os.environ)
    environ.pop(CONSOLE_URL_ENV, None)
    environ["AGENT_WORKER_UI_BIND"] = bind
    environ["AGENT_WORKER_UI_PORT"] = "18787"
    if override is not None:
        environ[CONSOLE_URL_ENV] = override
    result = subprocess.run(
        [
            compose,
            "compose",
            "--env-file",
            os.devnull,
            "-p",
            "console-regression",
            "-f",
            str(config),
            "config",
            "--format",
            "json",
        ],
        env=environ,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    resolved = json.loads(result.stdout)["services"]["worker"]["environment"][CONSOLE_URL_ENV]
    assert resolved == (override or "")
    labels = registration_config({}, {CONSOLE_URL_ENV: resolved})["labels"]
    assert labels == ({"console_url": override} if override else {})
