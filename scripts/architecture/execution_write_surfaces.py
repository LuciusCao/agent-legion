"""Execution write-surface registry guard (EXEC-GENERATION-002).

EXEC-GENERATION-001（docs/architecture/execution-generation.md）把执行态
（jobs / job_nodes / node_runs / executor_leases / agent_execution_requests）、
产物字节（artifact key 的 put_stream/copy_object）与产物清单行
（upsert_artifact_row_tx / ARTIFACT_ROW_UPSERT_SQL）三类写面收敛到共享
helper/primitive；多轮对抗审查反复发现的问题都是新写面绕过它们直写。本检查
把「写面全集」钉在 config/architecture/execution-write-surfaces.json：命中
注册表外的写面即报错，注册表里的条目若不再有对应写面（漂移）同样报错。

扫描方式与 broad_except_audit / sql_placeholders 一致：AST 字符串字面量找
SQL（多行 SQL 天然覆盖；拼接 SQL 与 ruff/mypy 一样归人工评审），AST Call 找
字节写调用。只扫 server/app 与 worker（测试目录不在扫描根内，与现有检查一致）。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

__test__ = False

REGISTRY_RELATIVE_PATH = "config/architecture/execution-write-surfaces.json"

_SCAN_ROOTS = ("server/app", "worker")

_STATE_TABLES = (
    "jobs",
    "job_nodes",
    "node_runs",
    "executor_leases",
    "agent_execution_requests",
)
_STATE_WRITE_RE = re.compile(
    r"\b(insert\s+into|update)\s+(only\s+){0,1}(" + "|".join(_STATE_TABLES) + r")\b",
    re.IGNORECASE,
)

_BYTE_WRITE_METHODS = ("put_stream", "copy_object")
# storage 抽象自身的实现（boto3 调用点）不是写面违规。
_BYTE_WRITE_EXEMPT_PREFIXES = ("server/app/storage/",)

_MANIFEST_WRITE_NAMES = ("upsert_artifact_row_tx", "ARTIFACT_ROW_UPSERT_SQL")
# upsert_artifact_row_tx 的定义模块（helper 本体不是调用点）。
_MANIFEST_DEF_MODULES = ("server/app/services/job_artifact_rows.py",)

_GUIDANCE = (
    "execution write surface outside config/architecture/execution-write-surfaces.json; "
    "新写面必须走共享 helper/primitive（lease_guarded_mutation / "
    "lock_job_mutation_and_read_generation / promote_to_authority_guarded / "
    "upsert_artifact_row_tx）并把条目加进注册表，检查清单见 "
    "docs/architecture/execution-generation.md §4.2"
)


class _WriteSurfaceConfigurationError(Exception):
    pass


def find_state_writes(source: str) -> list[tuple[int, str]]:
    """(行号, "update job_nodes" 形态标签) 列表：字符串字面量里对五表的写 SQL。"""
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        for match in _STATE_WRITE_RE.finditer(node.value):
            label = f"{match.group(1).lower()} {match.group(3).lower()}"
            hits.append((node.lineno, re.sub(r"\s+", " ", label)))
    return sorted(hits)


def find_artifact_byte_writes(source: str) -> list[int]:
    """put_stream( / copy_object( 调用点的 1-based 行号（Name 或 Attribute 形态）。"""
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else (func.id if isinstance(func, ast.Name) else "")
        )
        if name in _BYTE_WRITE_METHODS:
            lines.append(node.lineno)
    return sorted(lines)


def find_manifest_row_writes(source: str) -> list[int]:
    """upsert_artifact_row_tx / ARTIFACT_ROW_UPSERT_SQL 引用点的 1-based 行号。"""
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name in _MANIFEST_WRITE_NAMES for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in _MANIFEST_WRITE_NAMES:
                lines.append(node.lineno)
    return sorted(set(lines))


def load_write_surface_registry(root: Path) -> dict[str, dict[str, dict]]:
    """读取注册表；缺失按空注册表处理，结构非法时抛 _WriteSurfaceConfigurationError。"""
    path = root / REGISTRY_RELATIVE_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # 缺失按空注册表处理（与 sql_placeholders 缺 baseline 同惯例）：真实仓库里
        # 删掉注册表会让全部既有写面变成「未登记写面」而当场失败，不会静默放行；
        # 这也是 check_repository 的合成 tmp 仓库无需造注册表的原因。
        return {
            "state_write_modules": {},
            "artifact_byte_write_sites": {},
            "manifest_row_write_sites": {},
        }
    except json.JSONDecodeError as exc:
        raise _WriteSurfaceConfigurationError(f"registry is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise _WriteSurfaceConfigurationError("registry root must be an object")
    sections: dict[str, dict[str, dict]] = {}
    for key in ("state_write_modules", "artifact_byte_write_sites", "manifest_row_write_sites"):
        value = raw.get(key, {})
        if not isinstance(value, dict):
            raise _WriteSurfaceConfigurationError(f"registry section {key} must be an object")
        for entry_path, entry in value.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("via"), list):
                raise _WriteSurfaceConfigurationError(
                    f"registry entry {key}/{entry_path} must carry a 'via' list"
                )
        sections[key] = value
    return sections


def _iter_scan_files(root: Path):
    for base in _SCAN_ROOTS:
        directory = root / base
        if not directory.is_dir():
            continue
        yield from sorted(directory.rglob("*.py"))


def check_execution_write_surfaces(root: Path) -> list[str]:
    """拒绝注册表外的执行写面与已漂移的注册表条目。"""
    try:
        registry = load_write_surface_registry(root)
    except _WriteSurfaceConfigurationError as exc:
        return [f"execution write surface configuration: {exc}"]

    errors: list[str] = []
    observed: dict[str, set[str]] = {
        "state_write_modules": set(),
        "artifact_byte_write_sites": set(),
        "manifest_row_write_sites": set(),
    }
    for path in _iter_scan_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            state_hits = find_state_writes(source)
            byte_hits = find_artifact_byte_writes(source)
            manifest_hits = find_manifest_row_writes(source)
        except SyntaxError:
            continue  # 无法解析：ruff/mypy 负责这个失败
        if state_hits:
            observed["state_write_modules"].add(rel)
            if rel not in registry["state_write_modules"]:
                first, label = state_hits[0]
                errors.append(f"{rel}:{first}: {label} x{len(state_hits)} — {_GUIDANCE}")
        if byte_hits and not rel.startswith(_BYTE_WRITE_EXEMPT_PREFIXES):
            observed["artifact_byte_write_sites"].add(rel)
            if rel not in registry["artifact_byte_write_sites"]:
                errors.append(
                    f"{rel}:{byte_hits[0]}: put_stream/copy_object artifact byte write "
                    f"x{len(byte_hits)} — {_GUIDANCE}"
                )
        if manifest_hits and rel not in _MANIFEST_DEF_MODULES:
            observed["manifest_row_write_sites"].add(rel)
            if rel not in registry["manifest_row_write_sites"]:
                errors.append(
                    f"{rel}:{manifest_hits[0]}: manifest row upsert reference "
                    f"x{len(manifest_hits)} — {_GUIDANCE}"
                )

    for section, entries in registry.items():
        for entry_path in entries:
            if entry_path not in observed[section]:
                errors.append(
                    f"{REGISTRY_RELATIVE_PATH}: stale {section} entry {entry_path}: "
                    "文件不存在或已无对应写面——收缩注册表（写面全集与实际扫描必须一致）"
                )
    return sorted(errors)
