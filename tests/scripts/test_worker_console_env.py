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
    ],
)
def test_launchers_preserve_dotenv_precedence(tmp_path, script, inherited, content, configured):
    source = (ROOT / "scripts" / script).read_text()
    injection = re.search(
        r'    if \[\[ -z "\$\{AGENT_LEGION_WORKER_CONSOLE_URL\+x\}".*?^    fi',
        source,
        re.M | re.S,
    )
    assert injection
    normalize = re.search(r"^health_host\(\) \{.*?^\}", source, re.M | re.S)
    code = (normalize.group(0) if normalize else "") + "\nWORKER_BIND=127.0.0.1\nWORKER_PORT=8799\n"
    code += injection.group(0)
    # Run the real backend loader after shell default injection, in the same environment.
    code += '\nexec "$1" -c \'import os; from dotenv import load_dotenv; load_dotenv(".env", override=False); print(os.environ["AGENT_LEGION_WORKER_CONSOLE_URL"], end="")\'\n'
    if content is not None:
        (tmp_path / ".env").write_text(content)
    env = os.environ.copy()
    env.pop(KEY, None)
    env.pop("PYTHON_DOTENV_DISABLED", None)
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
