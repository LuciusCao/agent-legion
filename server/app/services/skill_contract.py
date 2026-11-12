"""Strict authoring-side validation for the skill-root ``contract.yaml``.

The velites parser remains authoritative at runtime. This module mirrors its
``deny_unknown_fields`` structure so authoring validation rejects a malformed
root before a repository mutation is committed. A missing root is not an error;
source-tier migration notices live in ``skill_contract_advisories``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml

_ALLOWED_FORMATS = ("text", "json")
_ALLOWED_FILE_KEYS = frozenset({"path", "format", "min_chars", "required_headings", "schema"})
_TEXT_ONLY_KEYS = frozenset({"min_chars", "required_headings"})


def contract_yaml_errors(content_dir: Path) -> list[dict[str, str]]:
    """Return strict root-contract errors; a missing root returns ``[]``.

    Present-but-malformed input produces structured errors and never falls
    back to an embedded block. Semantics mirror velites ``Contract::parse``.
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
    if min_chars is not None and (
        isinstance(min_chars, bool)  # bool is an int subclass; YAML `true` must not pass
        or not isinstance(min_chars, int)
        or min_chars < 0
    ):
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
