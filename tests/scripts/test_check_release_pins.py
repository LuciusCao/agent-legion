"""Contract tests for conservative pin discovery, logical lines, scalar/tag checks, and CLI."""

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

INSTALLER = """\
WORKER_VERSION_DEFAULT="0.8.6"
VELITES_VERSION_DEFAULT="0.5.11"
WORKER_VERSION="${AGENT_WORKER_VERSION:-$WORKER_VERSION_DEFAULT}"
VELITES_VERSION="${VELITES_VERSION:-$VELITES_VERSION_DEFAULT}"
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
    installer: str = INSTALLER,
    standalone: str = STANDALONE,
    pull_example: str = PULL_EXAMPLE,
) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "agent-legion"\nversion = "{repo_version}"\n', encoding="utf-8"
    )
    velites = root / "velites"
    velites.mkdir(parents=True)
    (velites / "Cargo.toml").write_text(
        f'[package]\nname = "velites"\nversion = "{velites_version}"\n', encoding="utf-8"
    )
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "install-worker.sh").write_text(installer, encoding="utf-8")
    deploy = root / "deploy"
    deploy.mkdir()
    (deploy / "compose.worker.standalone.yaml").write_text(standalone, encoding="utf-8")
    (deploy / "compose.worker.pull.example.yaml").write_text(pull_example, encoding="utf-8")


def test_fresh_pins_pass(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    errors, notes = pin_errors(tmp_path)
    assert errors == []
    assert len([note for note in notes if "✓" in note]) == 6


def test_stale_installer_worker_pin_is_rejected(tmp_path: Path) -> None:
    _write_repo(
        tmp_path,
        installer=INSTALLER.replace(
            'WORKER_VERSION_DEFAULT="0.8.6"', 'WORKER_VERSION_DEFAULT="0.8.5"'
        ),
    )
    errors, _ = pin_errors(tmp_path)
    assert any("WORKER_VERSION_DEFAULT" in error and "0.8.5" in error for error in errors)


def test_stale_installer_velites_pin_is_rejected(tmp_path: Path) -> None:
    _write_repo(tmp_path, velites_version="0.5.12")
    errors, _ = pin_errors(tmp_path)
    assert any("VELITES_VERSION_DEFAULT" in error and "0.5.11" in error for error in errors)


def test_stale_standalone_pins_are_rejected(tmp_path: Path) -> None:
    stale = STANDALONE.replace(f"{WORKER_IMAGE}:0.8.6", f"{WORKER_IMAGE}:0.6.0")
    _write_repo(tmp_path, standalone=stale)
    errors, _ = pin_errors(tmp_path)
    standalone_errors = [error for error in errors if "standalone" in error]
    assert len(standalone_errors) == 2
    assert all("0.6.0" in error for error in standalone_errors)


def test_stale_pull_example_environment_pin_is_rejected(tmp_path: Path) -> None:
    stale = PULL_EXAMPLE.replace(
        f"AGENT_WORKER_IMAGE_VERSION: {WORKER_IMAGE}:0.8.6",
        f"AGENT_WORKER_IMAGE_VERSION: {WORKER_IMAGE}:0.6.0",
    )
    _write_repo(tmp_path, pull_example=stale)
    errors, _ = pin_errors(tmp_path)
    assert len(errors) == 1
    assert "pull.example.yaml AGENT_WORKER_IMAGE_VERSION" in errors[0]


def test_prerelease_normalization_matches_worker_tag_forms(tmp_path: Path) -> None:
    _write_repo(
        tmp_path,
        repo_version="0.9.0a0",
        installer=INSTALLER.replace(
            'WORKER_VERSION_DEFAULT="0.8.6"', 'WORKER_VERSION_DEFAULT="0.9.0-alpha"'
        ),
        standalone=STANDALONE.replace(f"{WORKER_IMAGE}:0.8.6", f"{WORKER_IMAGE}:0.9.0-alpha"),
        pull_example=PULL_EXAMPLE.replace(f"{WORKER_IMAGE}:0.8.6", f"{WORKER_IMAGE}:0.9.0-alpha"),
    )
    errors, _ = pin_errors(tmp_path)
    assert errors == []


def test_velites_equivalent_prerelease_form_is_not_accepted(tmp_path: Path) -> None:
    installer = INSTALLER.replace(
        'VELITES_VERSION_DEFAULT="0.5.11"', 'VELITES_VERSION_DEFAULT="0.6.0-alpha"'
    )
    _write_repo(tmp_path, velites_version="0.6.0a0", installer=installer)
    errors, _ = pin_errors(tmp_path)
    assert any("VELITES_VERSION" in error for error in errors)


def test_worker_image_refix_suffix_is_accepted(tmp_path: Path) -> None:
    installer = INSTALLER.replace(
        'WORKER_VERSION_DEFAULT="0.8.6"', 'WORKER_VERSION_DEFAULT="0.8.6-r2"'
    )
    standalone = STANDALONE.replace(f"{WORKER_IMAGE}:0.8.6", f"{WORKER_IMAGE}:0.8.6-r2")
    _write_repo(tmp_path, installer=installer, standalone=standalone)
    errors, notes = pin_errors(tmp_path)
    assert errors == []
    assert any("standalone.yaml image: 0.8.6-r2 ✓" in note for note in notes)


def test_missing_installer_pin_is_fail_closed(tmp_path: Path) -> None:
    _write_repo(tmp_path, installer='WORKER_VERSION="${AGENT_WORKER_VERSION:-latest}"\n')
    errors, _ = pin_errors(tmp_path)
    assert any("找到 0 处" in error and "check_release_pins.py" in error for error in errors)


def test_missing_compose_file_is_fail_closed(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "deploy/compose.worker.pull.example.yaml").unlink()
    errors, _ = pin_errors(tmp_path)
    assert len([error for error in errors if "pull.example" in error]) == 2


def test_compose_image_does_not_mask_missing_environment_pin(tmp_path: Path) -> None:
    missing_env = STANDALONE.replace(
        f"      AGENT_WORKER_IMAGE_VERSION: ${{AGENT_WORKER_IMAGE:-{WORKER_IMAGE}:0.8.6}}\n",
        "",
    )
    _write_repo(tmp_path, standalone=missing_env)
    errors, _ = pin_errors(tmp_path)
    assert len(errors) == 1
    assert "AGENT_WORKER_IMAGE_VERSION" in errors[0]
    assert "找到 0 处" in errors[0]


def test_duplicate_compose_pin_is_fail_closed(tmp_path: Path) -> None:
    image_line = f"    image: {WORKER_IMAGE}:0.8.6\n"
    _write_repo(tmp_path, pull_example=PULL_EXAMPLE.replace(image_line, image_line * 2))
    errors, _ = pin_errors(tmp_path)
    assert any("pull.example.yaml image" in error and "找到 2 处" in error for error in errors)


def test_missing_manifest_is_fail_closed(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "velites/Cargo.toml").unlink()
    errors, _ = pin_errors(tmp_path)
    assert any("版本清单读取失败" in error for error in errors)


def test_missing_manifest_version_is_fail_closed(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "agent-legion"\n', encoding="utf-8")
    errors, _ = pin_errors(tmp_path)
    assert any("缺 project.version" in error for error in errors)


def test_malformed_manifest_is_fail_closed(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "velites/Cargo.toml").write_text("not = [valid", encoding="utf-8")
    errors, _ = pin_errors(tmp_path)
    assert any("版本清单读取失败" in error for error in errors)


def test_main_parses_process_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_repo(
        tmp_path,
        installer=INSTALLER.replace(
            'WORKER_VERSION_DEFAULT="0.8.6"', 'WORKER_VERSION_DEFAULT="0.6.0"'
        ),
    )
    monkeypatch.setattr(sys, "argv", ["check_release_pins", "--root", str(tmp_path)])
    assert check_release_pins.main() == 1


def test_direct_script_entrypoint(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_release_pins.py"), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_real_repository_pins_pass() -> None:
    errors, _ = pin_errors(ROOT)
    assert errors == []


def test_installer_usage_derives_from_single_default(tmp_path: Path) -> None:
    source = (ROOT / "scripts/install-worker.sh").read_text(encoding="utf-8")
    source, worker_count = re.subn(
        r'WORKER_VERSION_DEFAULT="[^"]+"', 'WORKER_VERSION_DEFAULT="9.8.7"', source
    )
    source, velites_count = re.subn(
        r'VELITES_VERSION_DEFAULT="[^"]+"', 'VELITES_VERSION_DEFAULT="6.5.4"', source
    )
    assert worker_count == velites_count == 1
    assert 'WORKER_VERSION="${AGENT_WORKER_VERSION:-$WORKER_VERSION_DEFAULT}"' in source
    assert 'VELITES_VERSION="${VELITES_VERSION:-$VELITES_VERSION_DEFAULT}"' in source
    installer = tmp_path / "install-worker.sh"
    installer.write_text(source, encoding="utf-8")
    result = subprocess.run(
        ["sh", str(installer), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "worker 镜像 tag（默认 9.8.7" in result.stdout
    assert "velites 二进制版本（默认 6.5.4）" in result.stdout


def test_real_standalone_retains_image_interpolation() -> None:
    source = (ROOT / "deploy/compose.worker.standalone.yaml").read_text(encoding="utf-8")
    version = check_release_pins.manifest_versions(ROOT)["agent-legion"]
    assert source.count(f"${{AGENT_WORKER_IMAGE:-{WORKER_IMAGE}:{version}}}") == 2


def _set_compose_value(document: str, field: str, value: str) -> str:
    pattern = re.compile(rf"^([ \t]*{field}:).*$", re.M)
    replaced, count = pattern.subn(rf"\g<1> {value}", document, count=1)
    assert count == 1
    return replaced


def _duplicate_compose_field(document: str, field: str, duplicate: str) -> str:
    line = next(line for line in document.splitlines() if line.lstrip().startswith(f"{field}:"))
    indent = line[: len(line) - len(line.lstrip())]
    return document.replace(line, f"{line}\n{indent}{duplicate.format(field=field)}", 1)


def _write_compose_repo(root: Path, name: str, document: str) -> None:
    if name == "standalone":
        _write_repo(root, standalone=document)
    else:
        _write_repo(root, pull_example=document)


@pytest.mark.parametrize(
    ("variable", "expected"),
    [("WORKER_VERSION_DEFAULT", "0.8.6"), ("VELITES_VERSION_DEFAULT", "0.5.11")],
)
@pytest.mark.parametrize(
    "shape",
    [
        "{variable}='0.6.0'",
        '{variable}=""',
        "{variable}=0.6.0",
        "export {variable}='0.6.0'",
        "readonly {variable}='0.6.0'",
    ],
)
def test_installer_discovers_every_duplicate_assignment_shape(
    tmp_path: Path, variable: str, expected: str, shape: str
) -> None:
    original = f'{variable}="{expected}"'
    duplicate = shape.format(variable=variable)
    _write_repo(tmp_path, installer=INSTALLER.replace(original, f"{original}\n{duplicate}", 1))
    errors, _ = pin_errors(tmp_path)
    assert any(variable in error and "找到 2 处" in error for error in errors)


@pytest.mark.parametrize(
    ("variable", "expected"),
    [("WORKER_VERSION_DEFAULT", "0.8.6"), ("VELITES_VERSION_DEFAULT", "0.5.11")],
)
@pytest.mark.parametrize(
    "shape",
    ["{variable}='{expected}'", '{variable}=""', "{variable}={expected}"],
)
def test_installer_rejects_invalid_unique_assignment_shape(
    tmp_path: Path, variable: str, expected: str, shape: str
) -> None:
    original = f'{variable}="{expected}"'
    _write_repo(
        tmp_path,
        installer=INSTALLER.replace(
            original, shape.format(variable=variable, expected=expected), 1
        ),
    )
    errors, _ = pin_errors(tmp_path)
    assert any(variable in error for error in errors)


@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
@pytest.mark.parametrize(
    "value",
    [
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6}-stale",
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6",
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6} trailing",
        "${AGENT_WORKER_IMAGE:-ghcr.io/luciuscao/agent-legion-worker:0.8.6}}",
        '""',
        "",
    ],
)
def test_standalone_consumes_complete_interpolated_scalar(
    tmp_path: Path, field: str, value: str
) -> None:
    document = _set_compose_value(STANDALONE, field, value)
    _write_compose_repo(tmp_path, "standalone", document)
    errors, _ = pin_errors(tmp_path)
    assert any("standalone" in error and field in error for error in errors)


@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
def test_standalone_rejects_fixed_scalar(tmp_path: Path, field: str) -> None:
    document = _set_compose_value(STANDALONE, field, f"{WORKER_IMAGE}:0.8.6")
    _write_compose_repo(tmp_path, "standalone", document)
    errors, _ = pin_errors(tmp_path)
    assert any("standalone" in error and field in error for error in errors)


@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
@pytest.mark.parametrize(
    "value",
    [
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6 trailing-junk",
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6#trailing-junk",
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6-junk",
        "ghcr.io/luciuscao/agent-legion-worker:0.8.6}",
        '"ghcr.io/luciuscao/agent-legion-worker:0.8.6"',
        "",
    ],
)
def test_pull_example_consumes_complete_fixed_scalar(
    tmp_path: Path, field: str, value: str
) -> None:
    document = _set_compose_value(PULL_EXAMPLE, field, value)
    _write_compose_repo(tmp_path, "pull.example", document)
    errors, _ = pin_errors(tmp_path)
    assert any("pull.example" in error and field in error for error in errors)


@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
def test_pull_example_rejects_interpolated_scalar(tmp_path: Path, field: str) -> None:
    value = f"${{AGENT_WORKER_IMAGE:-{WORKER_IMAGE}:0.8.6}}"
    document = _set_compose_value(PULL_EXAMPLE, field, value)
    _write_compose_repo(tmp_path, "pull.example", document)
    errors, _ = pin_errors(tmp_path)
    assert any("pull.example" in error and field in error for error in errors)


@pytest.mark.parametrize("name", ["standalone", "pull.example"])
@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
@pytest.mark.parametrize("duplicate", ['{field}: ""', "'{field}': ''"])
def test_compose_discovers_empty_and_quoted_duplicate_fields(
    tmp_path: Path, name: str, field: str, duplicate: str
) -> None:
    original = STANDALONE if name == "standalone" else PULL_EXAMPLE
    document = _duplicate_compose_field(original, field, duplicate)
    _write_compose_repo(tmp_path, name, document)
    errors, _ = pin_errors(tmp_path)
    assert any(name in error and field in error and "找到 2 处" in error for error in errors)


@pytest.mark.parametrize("name", ["standalone", "pull.example"])
@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
def test_compose_allows_comment_after_complete_value(tmp_path: Path, name: str, field: str) -> None:
    if name == "standalone":
        value = f"${{AGENT_WORKER_IMAGE:-{WORKER_IMAGE}:0.8.6}} # release pin"
        original = STANDALONE
    else:
        value = f"{WORKER_IMAGE}:0.8.6 # release pin"
        original = PULL_EXAMPLE
    document = _set_compose_value(original, field, value)
    _write_compose_repo(tmp_path, name, document)
    errors, _ = pin_errors(tmp_path)
    assert errors == []


def _continued_assignment(variable: str, value: str, *, internal: bool = False) -> str:
    continuation = "\\" + "\n"
    if internal:
        name = variable.replace("_DEFAULT", f"_{continuation}DEFAULT")
        return f'{name}="{value}"'
    return f'{variable}{continuation}="{value}"'


@pytest.mark.parametrize(
    ("variable", "expected"),
    [("WORKER_VERSION_DEFAULT", "0.8.6"), ("VELITES_VERSION_DEFAULT", "0.5.11")],
)
@pytest.mark.parametrize("internal", [False, True], ids=["before-equals", "inside-name"])
@pytest.mark.parametrize("value", ["0.6.0", ""], ids=["stale", "empty"])
def test_installer_discovers_continued_duplicate_assignments(
    tmp_path: Path, variable: str, expected: str, internal: bool, value: str
) -> None:
    original = f'{variable}="{expected}"'
    duplicate = _continued_assignment(variable, value, internal=internal)
    _write_repo(tmp_path, installer=INSTALLER.replace(original, f"{original}\n{duplicate}", 1))
    errors, _ = pin_errors(tmp_path)
    assert any(variable in error and "找到 2 处" in error for error in errors)


@pytest.mark.parametrize(
    ("variable", "expected"),
    [("WORKER_VERSION_DEFAULT", "0.8.6"), ("VELITES_VERSION_DEFAULT", "0.5.11")],
)
@pytest.mark.parametrize("internal", [False, True], ids=["before-equals", "inside-name"])
@pytest.mark.parametrize("outcome", ["current", "stale", "empty"])
def test_installer_unique_continuation_follows_normalized_shape(
    tmp_path: Path, variable: str, expected: str, internal: bool, outcome: str
) -> None:
    value = {"current": expected, "stale": "0.6.0", "empty": ""}[outcome]
    original = f'{variable}="{expected}"'
    continued = _continued_assignment(variable, value, internal=internal)
    _write_repo(tmp_path, installer=INSTALLER.replace(original, continued, 1))
    errors, _ = pin_errors(tmp_path)
    if outcome == "current":
        assert errors == []
    else:
        assert any(variable in error for error in errors)


@pytest.mark.parametrize(
    ("variable", "expected"),
    [("WORKER_VERSION_DEFAULT", "0.8.6"), ("VELITES_VERSION_DEFAULT", "0.5.11")],
)
@pytest.mark.parametrize("shell", ["sh", "dash"])
@pytest.mark.parametrize("duplicate", [True, False], ids=["duplicate-stale", "unique-current"])
def test_installer_continuation_matches_real_shell_and_gate(
    tmp_path: Path, variable: str, expected: str, shell: str, duplicate: bool
) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed")
    original = f'{variable}="{expected}"'
    continued = _continued_assignment(variable, "0.6.0" if duplicate else expected)
    replacement = f"{original}\n{continued}" if duplicate else continued
    source = (ROOT / "scripts/install-worker.sh").read_text(encoding="utf-8")
    source = source.replace(original, replacement, 1)
    _write_repo(tmp_path)
    installer = tmp_path / "scripts/install-worker.sh"
    installer.write_text(source, encoding="utf-8")

    result = subprocess.run([shell, str(installer), "--help"], capture_output=True, text=True)
    expected_version = "0.6.0" if duplicate else expected
    label = "worker 镜像 tag" if variable == "WORKER_VERSION_DEFAULT" else "velites 二进制版本"
    assert result.returncode == 0, result.stderr
    assert f"{label}（默认 {expected_version}" in result.stdout
    errors, _ = pin_errors(tmp_path)
    if duplicate:
        assert any(variable in error and "找到 2 处" in error for error in errors)
    else:
        assert errors == []


@pytest.mark.parametrize("name", ["standalone", "pull.example"])
@pytest.mark.parametrize("field", ["image", "AGENT_WORKER_IMAGE_VERSION"])
def test_compose_rejects_docker_incompatible_local_suffix(
    tmp_path: Path, name: str, field: str
) -> None:
    tag = "0.8.6+0.6.0"
    if name == "standalone":
        value = f"${{AGENT_WORKER_IMAGE:-{WORKER_IMAGE}:{tag}}}"
        original = STANDALONE
    else:
        value = f"{WORKER_IMAGE}:{tag}"
        original = PULL_EXAMPLE
    document = _set_compose_value(original, field, value)
    _write_compose_repo(tmp_path, name, document)
    errors, _ = pin_errors(tmp_path)
    assert any(name in error and field in error for error in errors)


COMMAND_ASSIGNMENT_FORMS = [
    ":; {assignment}",
    "true && {assignment}",
    "if true; then {assignment}; fi",
]


@pytest.mark.parametrize(
    ("variable", "expected"),
    [("WORKER_VERSION_DEFAULT", "0.8.6"), ("VELITES_VERSION_DEFAULT", "0.5.11")],
)
@pytest.mark.parametrize("form", COMMAND_ASSIGNMENT_FORMS)
@pytest.mark.parametrize("value", ["0.6.0", ""], ids=["stale", "empty"])
def test_installer_discovers_command_position_duplicates(
    tmp_path: Path, variable: str, expected: str, form: str, value: str
) -> None:
    original = f'{variable}="{expected}"'
    assignment = f'{variable}="{value}"'
    injected = form.format(assignment=assignment)
    _write_repo(tmp_path, installer=INSTALLER.replace(original, f"{original}\n{injected}", 1))
    errors, _ = pin_errors(tmp_path)
    assert any(variable in error and "找到 2 处" in error for error in errors)


@pytest.mark.parametrize(
    ("variable", "expected"),
    [("WORKER_VERSION_DEFAULT", "0.8.6"), ("VELITES_VERSION_DEFAULT", "0.5.11")],
)
@pytest.mark.parametrize("form", COMMAND_ASSIGNMENT_FORMS)
def test_installer_rejects_unique_noncanonical_assignment(
    tmp_path: Path, variable: str, expected: str, form: str
) -> None:
    original = f'{variable}="{expected}"'
    injected = form.format(assignment=original)
    _write_repo(tmp_path, installer=INSTALLER.replace(original, injected, 1))
    errors, _ = pin_errors(tmp_path)
    assert any(variable in error for error in errors)


@pytest.mark.parametrize(
    ("variable", "expected"),
    [("WORKER_VERSION_DEFAULT", "0.8.6"), ("VELITES_VERSION_DEFAULT", "0.5.11")],
)
@pytest.mark.parametrize("shell", ["sh", "dash"])
@pytest.mark.parametrize("form", COMMAND_ASSIGNMENT_FORMS)
def test_installer_command_position_matches_real_shell_and_gate(
    tmp_path: Path, variable: str, expected: str, shell: str, form: str
) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed")
    original = f'{variable}="{expected}"'
    injected = form.format(assignment=f'{variable}="0.6.0"')
    source = (ROOT / "scripts/install-worker.sh").read_text(encoding="utf-8")
    source = source.replace(original, f"{original}\n{injected}", 1)
    _write_repo(tmp_path)
    installer = tmp_path / "scripts/install-worker.sh"
    installer.write_text(source, encoding="utf-8")

    result = subprocess.run([shell, str(installer), "--help"], capture_output=True, text=True)
    label = "worker 镜像 tag" if variable == "WORKER_VERSION_DEFAULT" else "velites 二进制版本"
    assert result.returncode == 0, result.stderr
    assert f"{label}（默认 0.6.0" in result.stdout
    errors, _ = pin_errors(tmp_path)
    assert any(variable in error and "找到 2 处" in error for error in errors)


@pytest.mark.parametrize("variable", ["WORKER_VERSION_DEFAULT", "VELITES_VERSION_DEFAULT"])
def test_installer_comment_assignment_like_is_fail_closed(tmp_path: Path, variable: str) -> None:
    expected = "0.8.6" if variable == "WORKER_VERSION_DEFAULT" else "0.5.11"
    original = f'{variable}="{expected}"'
    commented = f'{original}\n# {variable}="0.6.0"'
    _write_repo(tmp_path, installer=INSTALLER.replace(original, commented, 1))
    errors, _ = pin_errors(tmp_path)
    assert any(variable in error and "找到 2 处" in error for error in errors)


def test_release_pin_gate_is_in_governance_phase() -> None:
    source = (ROOT / "scripts/check-quick-backend.sh").read_text(encoding="utf-8")
    governance = source.split("run_governance_checks() {", 1)[1].split("\n}", 1)[0]
    assert governance.count("python -m scripts.check_release_pins") == 1
    assert source.count("python -m scripts.check_release_pins") == 1
