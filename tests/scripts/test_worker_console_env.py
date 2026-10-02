"""Both launchers must preserve dotenv settings before backend override=False loading."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]
KEY = "AGENT_LEGION_WORKER_CONSOLE_URL"


@pytest.mark.parametrize("script", ["native-prod-up.sh", "dev_stack.sh"])
@pytest.mark.parametrize("inherited", [None, "", "https://process.example"])
@pytest.mark.parametrize(
    ("content", "configured"),
    [
        (None, None),
        ("# AGENT_LEGION_WORKER_CONSOLE_URL = ignored\n", None),
        ("AGENT_LEGION_WORKER_CONSOLE_URL_EXTRA = ignored\n", None),
        (f"{KEY}=https://worker.example\n", "https://worker.example"),
        (f" {KEY} = https://worker.example # comment\n", "https://worker.example"),
        (f"\texport\t{KEY}\t=\t'https://worker.example'\n", "https://worker.example"),
        (f'{KEY} = ""\n', ""),
        (f"export {KEY} =\n", ""),
        (f"{KEY} = https://old.example\n{KEY} =\n", ""),
        (f"'{KEY}' = ''\n", ""),
        (f"'{KEY}' = 'https://worker.example'\n", "https://worker.example"),
        (f'OTHER="first line\n{KEY}=https://not-config.example\nlast line"\n', None),
        (f"{KEY}\n", None),
    ],
)
def test_launchers_preserve_dotenv_precedence(tmp_path, script, inherited, content, configured):
    source = (ROOT / "scripts" / script).read_text()
    injection = re.findall(
        r"^    (?:console_host=|export AGENT_LEGION_WORKER_CONSOLE_DEFAULT_URL=).*$",
        source,
        re.M,
    )
    assert injection
    normalize = re.search(r"^health_host\(\) \{.*?^\}", source, re.M | re.S)
    code = (normalize.group(0) if normalize else "") + "\nWORKER_BIND=127.0.0.1\nWORKER_PORT=8799\n"
    code += "\n".join(injection)
    # Exercise the actual settings boundary after shell default injection.
    code += '\nexec "$1" -c \'from pathlib import Path; import server.app.settings as s; s.PROJECT_ROOT=Path.cwd(); print(s.load_settings(config_path=Path("settings.yaml")).executor_runtime.agent_workers.console_url, end="")\'\n'
    (tmp_path / "settings.yaml").write_text("{}\n")
    if content is not None:
        (tmp_path / ".env").write_text(content)
    env = os.environ.copy()
    env.pop(KEY, None)
    env.pop("PYTHON_DOTENV_DISABLED", None)
    env.pop("AGENT_LEGION_SKIP_DOTENV", None)
    env["PYTHONPATH"] = str(ROOT)
    if inherited is not None:
        env[KEY] = inherited
    result = subprocess.run(
        ["bash", "-eu", "-c", code, "console", sys.executable],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = inherited if inherited is not None else configured
    assert result.stdout == (expected if expected is not None else "http://127.0.0.1:8799")
