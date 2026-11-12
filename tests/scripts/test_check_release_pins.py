"""Behavioral and structured contract tests for the release-pin gate (#504/#1112)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_release_pins
from scripts.check_release_pins import pin_errors

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
WORKER_IMAGE = "ghcr.io/luciuscao/agent-legion-worker"
WORKER = "WORKER_VERSION_DEFAULT"
VELITES = "VELITES_VERSION_DEFAULT"
HELP_OUTPUT = """\
  --version TAG         worker 镜像 tag（默认 0.8.6；须存在 worker-v<TAG> 发布 tag）
  --velites-version VER velites 二进制版本（默认 0.5.11）
"""
STANDALONE = """\
services:
  worker:
    image: ${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6}
    environment:
      AGENT_WORKER_IMAGE_VERSION: ${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6}
"""
PULL_EXAMPLE = """\
services:
  worker:
    build: !reset null
    image: ghcr.io/luciuscao/agent-legion-worker:0.8.6
    environment:
      AGENT_WORKER_IMAGE_VERSION: ghcr.io/luciuscao/agent-legion-worker:0.8.6
"""


def _write_repo(
    root: Path,
    *,
    repo_version: str = "0.8.6",
    velites_version: str = "0.5.11",
    installer: str | None = None,
    standalone: str = STANDALONE,
    pull_example: str = PULL_EXAMPLE,
) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "agent-legion"\nversion = "{repo_version}"\n', encoding="utf-8"
    )
    (root / "velites").mkdir()
    (root / "velites/Cargo.toml").write_text(
        f'[package]\nname = "velites"\nversion = "{velites_version}"\n', encoding="utf-8"
    )
    (root / "scripts").mkdir()
    source = installer or (ROOT / "scripts/install-worker.sh").read_text(encoding="utf-8")
    (root / "scripts/install-worker.sh").write_text(source, encoding="utf-8")
    (root / "deploy").mkdir()
    (root / "deploy/compose.worker.standalone.yaml").write_text(standalone, encoding="utf-8")
    (root / "deploy/compose.worker.pull.example.yaml").write_text(pull_example, encoding="utf-8")


def _errors(root: Path) -> list[str]:
    return pin_errors(root)[0]


def _default(variable: str) -> str:
    return "0.8.6" if variable == WORKER else "0.5.11"


def _stale(variable: str) -> str:
    return "0.6.0" if variable == WORKER else "0.5.10"


def _help(root: Path, variable: str, value: str, shell: str) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed")
    result = subprocess.run(
        [shell, str(root / "scripts/install-worker.sh"), "--help"],
        cwd=root,
        env={"HOME": str(root / "home"), "AGENT_WORKER_INSTALL_DIR": str(root / "install")},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    label = "worker 镜像 tag" if variable == WORKER else "velites 二进制版本"
    assert result.returncode == 0, result.stderr
    assert f"{label}（默认 {value}" in result.stdout
    assert not (root / "install").exists()


def _inject(root: Path, variable: str, injection: str, *, replace: bool = False) -> None:
    original = f'{variable}="{_default(variable)}"'
    source = (root / "scripts/install-worker.sh").read_text(encoding="utf-8")
    replacement = injection if replace else f"{original}\n{injection}"
    assert original in source
    (root / "scripts/install-worker.sh").write_text(
        source.replace(original, replacement, 1), encoding="utf-8"
    )


def _continued(variable: str, value: str, *, internal: bool = False) -> str:
    continuation = "\\" + "\n"
    if internal:
        return f'{variable.replace("_DEFAULT", f"_{continuation}DEFAULT")}="{value}"'
    return f'{variable}{continuation}="{value}"'


def _injection(form: str, variable: str, value: str) -> str:
    if form == "continuation-before-equals":
        return _continued(variable, value)
    if form == "continuation-inside-name":
        return _continued(variable, value, internal=True)
    if form == "semicolon":
        return f':; {variable}="{value}"'
    if form == "and":
        return f'true && {variable}="{value}"'
    if form == "or":
        return f'false || {variable}="{value}"'
    if form == "if-block":
        return f'if true; then {variable}="{value}"; fi'
    if form == "export-quoted-name":
        return f"export '{variable}'=\"{value}\""
    if form == "export-escaped-equals":
        return f"export {variable}" + "\\" + f'="{value}"'
    if form == "readonly-quoted-name":
        return f"readonly '{variable}'=\"{value}\""
    if form == "internal-empty-quote":
        name = variable.replace("_DEFAULT", "_''DEFAULT")
        return f'export {name}="{value}"'
    if form == "internal-backslash":
        name = variable.replace("DEFAULT", "DEFA" + "\\" + "ULT")
        return f'export {name}="{value}"'
    if form == "single-quote":
        return f"{variable}='{value}'"
    if form == "unquoted":
        return f"{variable}={value}"
    return f'{form} {variable}="{value}"'


def test_fresh_repository_pass(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    assert _errors(tmp_path) == []


def test_real_repository_pass() -> None:
    assert _errors(ROOT) == []


def test_installer_usage_derives_from_single_default(tmp_path: Path) -> None:
    source = (ROOT / "scripts/install-worker.sh").read_text(encoding="utf-8")
    source = source.replace('WORKER_VERSION_DEFAULT="0.8.6"', 'WORKER_VERSION_DEFAULT="9.8.7"', 1)
    source = source.replace(
        'VELITES_VERSION_DEFAULT="0.5.11"', 'VELITES_VERSION_DEFAULT="6.5.4"', 1
    )
    _write_repo(
        tmp_path,
        repo_version="9.8.7",
        velites_version="6.5.4",
        installer=source,
        standalone=STANDALONE.replace("0.8.6", "9.8.7"),
        pull_example=PULL_EXAMPLE.replace("0.8.6", "9.8.7"),
    )
    assert _errors(tmp_path) == []
    _help(tmp_path, WORKER, "9.8.7", "sh")
    _help(tmp_path, VELITES, "6.5.4", "dash")


def _purpose_line(source: str, variable: str, kind: str) -> str:
    if kind == "canonical":
        return f'{variable}="{_default(variable)}"'
    if kind == "effective":
        return next(
            line
            for line in source.splitlines()
            if line.startswith(f"{variable.removesuffix('_DEFAULT')}=")
        )
    return next(
        line
        for line in source.splitlines()
        if line.lstrip().startswith("printf") and variable in line
    )


@pytest.mark.parametrize("variable", [WORKER, VELITES])
@pytest.mark.parametrize(
    "case",
    [
        "stale",
        "missing",
        "empty",
        "single-quote",
        "duplicate",
        "effective",
        "usage",
        "duplicate-usage",
    ],
)
def test_installer_static_single_point_contract(tmp_path: Path, variable: str, case: str) -> None:
    source = (ROOT / "scripts/install-worker.sh").read_text(encoding="utf-8")
    canonical = _purpose_line(source, variable, "canonical")
    if case == "stale":
        source = source.replace(canonical, f'{variable}="{_stale(variable)}"', 1)
    elif case == "missing":
        source = source.replace(f"{canonical}\n", "", 1)
    elif case == "empty":
        source = source.replace(canonical, f'{variable}=""', 1)
    elif case == "single-quote":
        source = source.replace(canonical, f"{variable}='{_default(variable)}'", 1)
    elif case == "duplicate":
        source = source.replace(canonical, f"{canonical}\n{canonical}", 1)
    else:
        kind = "effective" if case == "effective" else "usage"
        line = _purpose_line(source, variable, kind)
        replacement = (
            f"{line}\n{line}"
            if case == "duplicate-usage"
            else line.replace(":-", ":+", 1)
            if kind == "effective"
            else line.replace("printf", "PRINTF", 1)
        )
        source = source.replace(line, replacement, 1)
    _write_repo(tmp_path, installer=source)
    assert any(variable in error for error in _errors(tmp_path))


HISTORICAL_FORMS = [
    "single-quote",
    "unquoted",
    "export",
    "readonly",
    "continuation-before-equals",
    "continuation-inside-name",
    "semicolon",
    "and",
    "or",
    "if-block",
    "export-quoted-name",
    "export-escaped-equals",
    "readonly-quoted-name",
    "internal-empty-quote",
    "internal-backslash",
]


@pytest.mark.parametrize("variable", [WORKER, VELITES])
@pytest.mark.parametrize("form", HISTORICAL_FORMS)
@pytest.mark.parametrize("value", ["stale", "empty"])
def test_historical_shell_forms_change_real_default_and_fail(
    tmp_path: Path, variable: str, form: str, value: str
) -> None:
    _write_repo(tmp_path)
    actual = _stale(variable) if value == "stale" else ""
    _inject(tmp_path, variable, _injection(form, variable, actual))
    _help(tmp_path, variable, actual, "sh")
    _help(tmp_path, variable, actual, "dash")
    assert any(variable in error for error in _errors(tmp_path))


@pytest.mark.parametrize("variable", [WORKER, VELITES])
@pytest.mark.parametrize("internal", [False, True], ids=["before-equals", "inside-name"])
@pytest.mark.parametrize("outcome", ["current", "stale", "empty"])
def test_unique_continuation_matches_real_shell_and_gate(
    tmp_path: Path, variable: str, internal: bool, outcome: str
) -> None:
    _write_repo(tmp_path)
    value = (
        _default(variable)
        if outcome == "current"
        else _stale(variable)
        if outcome == "stale"
        else ""
    )
    _inject(tmp_path, variable, _continued(variable, value, internal=internal), replace=True)
    _help(tmp_path, variable, value, "sh")
    _help(tmp_path, variable, value, "dash")
    errors = _errors(tmp_path)
    if outcome == "current":
        assert errors == []
    else:
        assert any(variable in error for error in errors)


@pytest.mark.parametrize("variable", [WORKER, VELITES])
@pytest.mark.parametrize("form", ["semicolon", "and", "if-block"])
def test_unique_noncanonical_assignment_fails_static_contract(
    tmp_path: Path, variable: str, form: str
) -> None:
    _write_repo(tmp_path)
    _inject(tmp_path, variable, _injection(form, variable, _default(variable)), replace=True)
    _help(tmp_path, variable, _default(variable), "sh")
    assert any(variable in error for error in _errors(tmp_path))


def test_help_nonzero_is_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_repo(tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "boom")
    )
    assert any("--help" in error for error in _errors(tmp_path))


def test_help_timeout_is_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_repo(tmp_path)

    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="/bin/sh", timeout=10)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert any("超时" in error for error in _errors(tmp_path))


@pytest.mark.parametrize(
    "stdout", ["", HELP_OUTPUT + "  --version TAG         worker 镜像 tag（默认 0.8.6）\n"]
)
def test_help_missing_or_duplicate_output_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    _write_repo(tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout, "")
    )
    assert any("--help" in error for error in _errors(tmp_path))


def test_help_side_effect_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_repo(tmp_path)

    def create_target(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        Path(env["AGENT_WORKER_INSTALL_DIR"]).mkdir()
        return subprocess.CompletedProcess(args, 0, HELP_OUTPUT, "")

    monkeypatch.setattr(subprocess, "run", create_target)
    assert any("创建" in error for error in _errors(tmp_path))


def test_help_uses_isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_repo(tmp_path)
    observed: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(args, 0, HELP_OUTPUT, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _errors(tmp_path) == []
    env = observed["env"]
    assert "AGENT_WORKER_VERSION" not in env
    assert "VELITES_VERSION" not in env
    assert env["HOME"] == observed["cwd"]
    assert observed["timeout"] == 10
    assert observed["capture_output"] is True
    assert observed["text"] is True


def _set_compose_value(document: str, field: str, value: str) -> str:
    pattern = re.compile(rf"^([ \t]*{field}:).*$", re.M)
    replaced, count = pattern.subn(rf"\g<1> {value}", document, count=1)
    assert count == 1
    return replaced


def _write_compose_repo(root: Path, name: str, document: str) -> None:
    if name == "standalone":
        _write_repo(root, standalone=document)
    else:
        _write_repo(root, pull_example=document)


@pytest.mark.parametrize("name", ["standalone", "pull.example"])
@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
def test_compose_stale_value_is_rejected(tmp_path: Path, name: str, field: str) -> None:
    original = STANDALONE if name == "standalone" else PULL_EXAMPLE
    document = _set_compose_value(
        original,
        field,
        original.split(f"{field}:", 1)[1].splitlines()[0].strip().replace("0.8.6", "0.6.0"),
    )
    _write_compose_repo(tmp_path, name, document)
    assert any(name in error and field in error for error in _errors(tmp_path))


@pytest.mark.parametrize("tag", ["0.9.0-alpha", "0.8.6-r2"])
def test_prerelease_and_refix_are_accepted(tmp_path: Path, tag: str) -> None:
    manifest = "0.9.0a0" if tag.startswith("0.9") else "0.8.6"
    source = (
        (ROOT / "scripts/install-worker.sh")
        .read_text(encoding="utf-8")
        .replace('WORKER_VERSION_DEFAULT="0.8.6"', f'WORKER_VERSION_DEFAULT="{tag}"', 1)
    )
    _write_repo(
        tmp_path,
        repo_version=manifest,
        installer=source,
        standalone=STANDALONE.replace("0.8.6", tag),
        pull_example=PULL_EXAMPLE.replace("0.8.6", tag),
    )
    assert _errors(tmp_path) == []


@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
@pytest.mark.parametrize(
    "value",
    [
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6}-stale",
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6",
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6} trailing",
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6}}",
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6",
        '"${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6}"',
        "",
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6+0.6.0}",
    ],
)
def test_standalone_rejects_invalid_scalar(tmp_path: Path, field: str, value: str) -> None:
    _write_compose_repo(tmp_path, "standalone", _set_compose_value(STANDALONE, field, value))
    assert any("standalone" in error and field in error for error in _errors(tmp_path))


@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
@pytest.mark.parametrize(
    "value",
    [
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6 trailing-junk",
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6#trailing-junk",
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6-junk",
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6}",
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6}",
        '"ghcr.io/luciuscao/agent-legion-worker:0.8.6"',
        "",
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6+0.6.0",
    ],
)
def test_pull_rejects_invalid_scalar(tmp_path: Path, field: str, value: str) -> None:
    _write_compose_repo(tmp_path, "pull.example", _set_compose_value(PULL_EXAMPLE, field, value))
    assert any("pull.example" in error and field in error for error in _errors(tmp_path))


@pytest.mark.parametrize("name", ["standalone", "pull.example"])
@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
def test_compose_allows_comment_after_complete_value(tmp_path: Path, name: str, field: str) -> None:
    original = STANDALONE if name == "standalone" else PULL_EXAMPLE
    value = original.split(f"{field}:", 1)[1].splitlines()[0].strip() + " # release pin"
    _write_compose_repo(tmp_path, name, _set_compose_value(original, field, value))
    assert _errors(tmp_path) == []


def _duplicate_field(document: str, field: str, form: str) -> str:
    line = next(line for line in document.splitlines() if line.lstrip().startswith(f"{field}:"))
    indent = line[: len(line) - len(line.lstrip())]
    key = (
        f"{field} :" if form == "whitespace" else f"'{field}':" if form == "quoted" else f"{field}:"
    )
    duplicate = f"{indent}{key} {line.split(':', 1)[1].strip().replace('0.8.6', '0.6.0')}"
    return document.replace(line, f"{line}\n{duplicate}", 1)


@pytest.mark.parametrize("name", ["standalone", "pull.example"])
@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
@pytest.mark.parametrize("form", ["plain", "whitespace", "quoted"])
def test_compose_duplicate_keys_fail(tmp_path: Path, name: str, field: str, form: str) -> None:
    original = STANDALONE if name == "standalone" else PULL_EXAMPLE
    _write_compose_repo(tmp_path, name, _duplicate_field(original, field, form))
    assert any(name in error for error in _errors(tmp_path))


@pytest.mark.parametrize(
    "field", ["services", "worker", "environment", "image", "AGENT_WORKER_IMAGE_VERSION"]
)
def test_compose_missing_structure_fails(tmp_path: Path, field: str) -> None:
    document = re.sub(rf"^[ \t]*{field}:.*\n", "", STANDALONE, count=1, flags=re.M)
    _write_compose_repo(tmp_path, "standalone", document)
    assert any("standalone" in error for error in _errors(tmp_path))


@pytest.mark.parametrize(
    "document",
    [
        "- item\n",
        "services: []\n",
        "services:\n  worker: []\n",
        "services:\n  worker:\n    image: []\n    environment: {}\n",
        "services:\n  worker:\n    image: ghcr.io/luciuscao/agent-legion-worker:0.8.6\n    environment: []\n",
        "services:\n  worker:\n    image: ghcr.io/luciuscao/agent-legion-worker:0.8.6\n    environment:\n      AGENT_WORKER_IMAGE_VERSION: []\n",
    ],
)
def test_compose_wrong_structure_types_fail(tmp_path: Path, document: str) -> None:
    _write_compose_repo(tmp_path, "standalone", document)
    assert any("standalone" in error for error in _errors(tmp_path))


@pytest.mark.parametrize("field", ["services", "worker", "environment"])
def test_compose_duplicate_structure_keys_fail(tmp_path: Path, field: str) -> None:
    line = next(line for line in STANDALONE.splitlines() if line.lstrip().startswith(f"{field}:"))
    _write_compose_repo(tmp_path, "standalone", STANDALONE.replace(line, f"{line}\n{line}", 1))
    assert any("standalone" in error for error in _errors(tmp_path))


@pytest.mark.parametrize("name", ["standalone", "pull.example"])
@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
@pytest.mark.parametrize(
    "value", ['""', "'ghcr.io/luciuscao/agent-legion-worker:0.8.6'", "[]", "null"]
)
def test_compose_nonplain_or_nonstring_values_fail(
    tmp_path: Path, name: str, field: str, value: str
) -> None:
    original = STANDALONE if name == "standalone" else PULL_EXAMPLE
    _write_compose_repo(tmp_path, name, _set_compose_value(original, field, value))
    assert any(name in error and field in error for error in _errors(tmp_path))


@pytest.mark.parametrize("name", ["standalone", "pull.example"])
@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
def test_compose_quoted_keys_are_parsed_structurally(tmp_path: Path, name: str, field: str) -> None:
    original = STANDALONE if name == "standalone" else PULL_EXAMPLE
    _write_compose_repo(tmp_path, name, original.replace(f"{field}:", f"'{field}':", 1))
    assert _errors(tmp_path) == []


def test_missing_compose_file_fails(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "deploy/compose.worker.pull.example.yaml").unlink()
    assert any("pull.example" in error for error in _errors(tmp_path))


def test_malformed_yaml_fails(tmp_path: Path) -> None:
    _write_repo(tmp_path, standalone="services: [unterminated")
    assert any("standalone" in error for error in _errors(tmp_path))


def test_missing_manifest_fails(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "velites/Cargo.toml").unlink()
    assert any("版本清单读取失败" in error for error in _errors(tmp_path))


def test_missing_manifest_version_fails(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "agent-legion"\n', encoding="utf-8")
    assert any("版本清单" in error or "发布线" in error for error in _errors(tmp_path))


def test_malformed_manifest_fails(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "velites/Cargo.toml").write_text("not = [valid", encoding="utf-8")
    assert any("版本清单读取失败" in error for error in _errors(tmp_path))


def test_main_parses_process_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_repo(tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_release_pins", "--root", str(tmp_path)])
    assert check_release_pins.main() == 0


def test_direct_script_entrypoint(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_release_pins.py"), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_release_pin_gate_is_in_governance_phase() -> None:
    source = (ROOT / "scripts/check-quick-backend.sh").read_text(encoding="utf-8")
    governance = source.split("run_governance_checks() {", 1)[1].split("\n}", 1)[0]
    assert governance.count("python -m scripts.check_release_pins") == 1
    assert source.count("python -m scripts.check_release_pins") == 1
