"""Shared path/contract checks for skill repo mutation services (#633, #542).

``SkillEditingService.save_version`` (services/skill_editing.py) and
``SkillCreationService.create_skill`` (services/skill_creation.py) validate
skill file paths and the runtime contract identically; the helpers live
here once so the two flows cannot drift (extracted from skill_editing.py,
no behaviour change).

#542 adds the graded contract validation. The runtime trio
(``SKILL.md`` + ``references/output-contract.md`` + ``scripts/
validate_output.py``) stays a hard error when missing (the format layer),
a malformed root ``contract.yaml`` is likewise an error (bad YAML, unknown
fields, empty ``files``, escaping/absolute paths, illegal format values,
text/json key misuse, uncompilable schema — semantic parity with the
velites ``Contract::parse`` deny_unknown_fields structure), and a MISSING
contract is only a warning: the embedded block still works (deprecated)
and a contract-less skill keeps running with existence-mode validation.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml

_ALLOWED_FORMATS = ("text", "json")
_ALLOWED_FILE_KEYS = frozenset({"path", "format", "min_chars", "required_headings", "schema"})
_TEXT_ONLY_KEYS = frozenset({"min_chars", "required_headings"})
_EMBEDDED_WARNING = (
    "contract.yaml is missing; the machine contract still lives in the "
    "deprecated embedded block (migrate it to a root contract.yaml)"
)
_MISSING_WARNING = (
    "contract.yaml is missing; runtime output validation degrades to "
    "existence-only mode (add a root contract.yaml)"
)


def contract_errors(content_dir: Path) -> list[dict[str, str]]:
    """The runtime skill contract (``workflows/skills.py`` enforces it at
    dispatch): non-empty SKILL.md + references/output-contract.md +
    scripts/validate_output.py, reported as a structured error list."""
    if not content_dir.is_dir():
        return [{"path": ".", "error": "skill directory does not exist"}]
    errors: list[dict[str, str]] = []
    skill_md = content_dir / "SKILL.md"
    if not skill_md.is_file():
        errors.append({"path": "SKILL.md", "error": "missing SKILL.md"})
    elif not skill_md.read_text(encoding="utf-8", errors="replace").strip():
        errors.append({"path": "SKILL.md", "error": "SKILL.md is empty"})
    for required in ("references/output-contract.md", "scripts/validate_output.py"):
        if not (content_dir / required).is_file():
            errors.append({"path": required, "error": f"missing {required}"})
    return errors


def contract_yaml_errors(content_dir: Path) -> list[dict[str, str]]:
    """Strict validation of the root ``contract.yaml`` (#542 format layer).

    Missing file → ``[]`` (a missing contract is a warning, not an error —
    see :func:`contract_warnings`). Present-but-malformed → one structured
    error per problem. Semantics mirror the velites engine
    (``Contract::parse`` + its deny_unknown_fields structure) so the two
    validations cannot drift.
    """
    path = content_dir / "contract.yaml"
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        return [{"path": "contract.yaml", "error": f"unreadable contract.yaml: {exc}"}]
    try:
        document = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        return [
            {
                "path": "contract.yaml",
                "error": f"invalid contract YAML: {exc.problem}"
                if hasattr(exc, "problem") and exc.problem
                else f"invalid contract YAML: {exc}",
            }
        ]
    if not isinstance(document, dict):
        return [{"path": "contract.yaml", "error": "contract.yaml must be a mapping"}]
    unknown_top = set(document) - {"files"}
    if unknown_top:
        return [
            {
                "path": "contract.yaml",
                "error": "unknown field(s): " + ", ".join(sorted(unknown_top)),
            }
        ]
    files = document.get("files")
    if not isinstance(files, list) or not files:
        return [
            {
                "path": "contract.yaml",
                "error": "`files` must be a non-empty list",
            }
        ]
    errors: list[dict[str, str]] = []
    for index, entry in enumerate(files):
        errors.extend(_file_entry_errors(entry, index))
    return errors


def _file_entry_errors(entry: object, index: int) -> list[dict[str, str]]:
    where = f"contract.yaml (files[{index}])"
    if not isinstance(entry, dict):
        return [{"path": where, "error": "each files[] entry must be a mapping"}]
    unknown = set(entry) - _ALLOWED_FILE_KEYS
    if unknown:
        return [
            {
                "path": where,
                "error": "unknown field(s): " + ", ".join(sorted(unknown)),
            }
        ]
    path_value = entry.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        return [{"path": where, "error": "`path` must be a non-empty string"}]
    errors: list[dict[str, str]] = []
    parts = PurePosixPath(path_value).parts
    if PurePosixPath(path_value).is_absolute():
        errors.append({"path": where, "error": f"`path` must be relative, got `{path_value}`"})
    elif ".." in parts:
        errors.append({"path": where, "error": f"`path` must not contain `..`, got `{path_value}`"})
    fmt = entry.get("format")
    if fmt not in _ALLOWED_FORMATS:
        errors.append(
            {
                "path": where,
                "error": f"`format` must be one of {_ALLOWED_FORMATS}, got `{fmt}`",
            }
        )
        return errors
    if fmt == "json":
        for key in _TEXT_ONLY_KEYS:
            if entry.get(key) is not None:
                errors.append(
                    {
                        "path": where,
                        "error": f"`{key}` only applies to `format: text`",
                    }
                )
        schema = entry.get("schema")
        if schema is None:
            errors.append({"path": where, "error": "`format: json` requires a `schema`"})
        else:
            errors.extend(_schema_errors(schema, where))
    elif entry.get("schema") is not None:
        errors.append({"path": where, "error": "`schema` only applies to `format: json`"})
    min_chars = entry.get("min_chars")
    if min_chars is not None and (not isinstance(min_chars, int) or min_chars < 0):
        errors.append({"path": where, "error": "`min_chars` must be a non-negative int"})
    headings = entry.get("required_headings")
    if headings is not None and (
        not isinstance(headings, list) or not all(isinstance(item, str) for item in headings)
    ):
        errors.append({"path": where, "error": "`required_headings` must be a list of strings"})
    return errors


def _schema_errors(schema: object, where: str) -> list[dict[str, str]]:
    """Compile-check a JSON Schema the way the velites engine would
    (draft 2020-12); a schema that cannot compile is an error."""
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a hard dependency
        return [
            {
                "path": where,
                "error": "schema validation unavailable (jsonschema not installed)",
            }
        ]
    try:
        jsonschema.Draft202012Validator.check_schema(schema)  # type: ignore[arg-type]
    except jsonschema.SchemaError as exc:
        return [{"path": where, "error": f"invalid JSON Schema: {exc.message}"}]
    except TypeError:
        return [{"path": where, "error": "`schema` must be a JSON Schema object"}]
    return []


def contract_warnings(content_dir: Path) -> list[dict[str, str]]:
    """Missing-contract warnings (#542 tier: warning, not error).

    A contract.yaml that is PRESENT but malformed yields no warning here
    (that is :func:`contract_yaml_errors`' error channel). Only a missing
    root file warns, phrased per the fallback tier: embedded block →
    deprecation nudge; nothing at all → existence-mode degradation notice.
    """
    from server.app.skills.contract_probe import probe_contract

    tier = probe_contract(content_dir)
    if tier == "embedded_block":
        return [{"path": "contract.yaml", "error": _EMBEDDED_WARNING}]
    if tier in ("none", "undetermined"):
        return [{"path": "contract.yaml", "error": _MISSING_WARNING}]
    return []


def graded_contract_check(content_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """The #542 graded validation as one call: (errors, warnings).

    Errors = the runtime trio plus a malformed root contract.yaml (the
    format layer); warnings = the missing-contract notices. The single
    entry point keeps validate/save_version from re-assembling the two
    channels differently.
    """
    errors = contract_errors(content_dir)
    errors.extend(contract_yaml_errors(content_dir))
    return errors, contract_warnings(content_dir)


def target_path_errors(raw_paths: list[str]) -> list[dict[str, str]]:
    """Path-safety errors for the given relative paths (empty list = ok):
    non-empty, relative, no ``..``, no ``.git`` at any level or case (on
    case-insensitive filesystems ``.GIT/hooks/`` still lands inside the git
    metadata dir)."""
    errors: list[dict[str, str]] = []
    for raw in raw_paths:
        parts = PurePosixPath(raw).parts
        if (
            not raw
            or PurePosixPath(raw).is_absolute()
            or ".." in parts
            or any(part.lower() == ".git" for part in parts)
        ):
            errors.append(
                {
                    "path": raw or ".",
                    "error": "path must be relative, stay inside the skill directory, "
                    "and not touch .git",
                }
            )
    return errors


def resolve_targets_checked(
    root_dir: Path, files: list[tuple[str, str]]
) -> tuple[list[tuple[Path, str]], list[dict[str, str]]]:
    """Resolve (targets, errors): every path either resolves inside root_dir
    (staying under it after symlink resolution) or lands in errors."""
    errors = target_path_errors([raw for raw, _ in files])
    rejected = {error["path"] for error in errors}
    targets: list[tuple[Path, str]] = []
    root = root_dir.resolve()
    for raw, content in files:
        if (raw or ".") in rejected:
            continue
        resolved = (root / raw).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            errors.append({"path": raw, "error": "path escapes the skill directory"})
            continue
        targets.append((resolved, content))
    return targets, errors
