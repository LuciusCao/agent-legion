"""Consistency tests for the artifact-identity state-space grid (#876).

Grid anti-rot for docs/architecture/artifact-identity-state-space.md,
mirroring test_evidence_matrix.py's pattern: every ✅ cell must name at
least one test that really exists (file exists; ``::symbol`` resolved
through the module AST), every 🧱 cell must carry a non-empty structural
argument, ⬜ and any fourth state are rejected (the delivered grid is
closed-world complete), and axis tokens must stay inside the declared
legend (C1-C8 / E1-E6).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRID_PATH = PROJECT_ROOT / "docs" / "architecture" / "artifact-identity-state-space.md"

REQUIRED_COLUMNS = ["不变量", "轴", "状态", "证据 / 论证"]
ALLOWED_STATES = {"✅", "🧱"}
ALLOWED_INVARIANTS = {f"INV-{i}" for i in range(1, 9)}
ALLOWED_AXES = {f"C{i}" for i in range(1, 9)} | {f"E{i}" for i in range(1, 7)}
_AXIS_TOKEN_RE = re.compile(r"[CE]\d+")
_TEST_REF_RE = re.compile(r"`(tests/[^`]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*)?)`")


def _parse_grid_table(text: str) -> list[dict[str, str]]:
    """Locate and parse the matrix table (the one with the required columns)."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells != REQUIRED_COLUMNS:
            continue
        rows: list[dict[str, str]] = []
        for data_line in lines[idx + 2 :]:  # skip the separator row
            if "|" not in data_line:
                break
            data_cells = [cell.strip() for cell in data_line.strip().strip("|").split("|")]
            if len(data_cells) != len(REQUIRED_COLUMNS):
                raise AssertionError(
                    f"row has {len(data_cells)} columns but header has "
                    f"{len(REQUIRED_COLUMNS)}: {data_line!r}"
                )
            rows.append(dict(zip(REQUIRED_COLUMNS, data_cells, strict=True)))
        return rows
    raise AssertionError(f"could not find the grid table (columns {REQUIRED_COLUMNS!r})")


def _check_test_ref(ref: str, root: Path) -> str | None:
    """None = resolves; otherwise the error message."""
    file_part, _, symbol = ref.partition("::")
    file_path = root / file_part
    if not file_path.exists():
        return f"test file does not exist: {file_part}"
    if not symbol:
        return None
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=file_part)
    symbols = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    if symbol not in symbols:
        return f"test symbol {symbol!r} not found in {file_part}"
    return None


def validate_grid(text: str, root: Path) -> list[str]:
    """Validate the grid matrix; returns the list of violations (empty = clean)."""
    errors: list[str] = []
    for row in _parse_grid_table(text):
        where = f"[{row['不变量']} × {row['轴']}]"
        if row["不变量"] not in ALLOWED_INVARIANTS:
            errors.append(f"{where} unknown invariant id {row['不变量']!r}")
        axes = _AXIS_TOKEN_RE.findall(row["轴"])
        if not axes or any(axis not in ALLOWED_AXES for axis in axes):
            errors.append(f"{where} axis tokens outside the legend (C1-C8/E1-E6): {row['轴']!r}")
        state, evidence = row["状态"], row["证据 / 论证"]
        if state not in ALLOWED_STATES:
            errors.append(f"{where} bare cell — state must be one of {sorted(ALLOWED_STATES)}")
            continue
        refs = _TEST_REF_RE.findall(evidence)
        for ref in refs:
            problem = _check_test_ref(ref, root)
            if problem is not None:
                errors.append(f"{where} {problem}")
        if state == "✅" and not refs:
            errors.append(f"{where} ✅ cell names no test")
        prose = _TEST_REF_RE.sub("", evidence).strip(" ，。;；、")
        if state == "🧱" and len(prose) < 10:
            errors.append(f"{where} 🧱 cell must carry a structural argument")
    return errors


def test_grid_file_exists() -> None:
    assert GRID_PATH.exists(), "state-space grid document is missing"


def test_grid_is_consistent() -> None:
    errors = validate_grid(GRID_PATH.read_text(encoding="utf-8"), PROJECT_ROOT)
    assert not errors, "grid inconsistencies:\n" + "\n".join(errors)


# --- checker self-tests (negative paths) ---

_HEADER = "| 不变量 | 轴 | 状态 | 证据 / 论证 |\n|---|---|---|---|\n"


def _grid(*rows: str) -> str:
    return _HEADER + "\n".join(rows) + "\n"


def test_checker_rejects_bare_cells() -> None:
    errors = validate_grid(_grid("| INV-1 | E1 | ⬜ | 待补 |"), PROJECT_ROOT)
    assert any("bare cell" in error for error in errors)


def test_checker_rejects_testless_verified_cells() -> None:
    errors = validate_grid(_grid("| INV-1 | E1 | ✅ | 没有点名测试 |"), PROJECT_ROOT)
    assert any("names no test" in error for error in errors)


def test_checker_rejects_argumentless_structural_cells() -> None:
    errors = validate_grid(_grid("| INV-1 | E1 | 🧱 | 不可达 |"), PROJECT_ROOT)
    assert any("structural argument" in error for error in errors)


def test_checker_rejects_missing_test_targets(tmp_path: Path) -> None:
    ghost = "| INV-1 | E1 | ✅ | `tests/workflows/test_ghost.py::test_nope` |"
    errors = validate_grid(_grid(ghost), tmp_path)
    assert any("does not exist" in error for error in errors)


def test_checker_rejects_missing_test_symbols(tmp_path: Path) -> None:
    real = tmp_path / "tests" / "workflows"
    real.mkdir(parents=True)
    (real / "test_real.py").write_text("def test_other():\n    pass\n")
    row = "| INV-1 | E1 | ✅ | `tests/workflows/test_real.py::test_nope` |"
    errors = validate_grid(_grid(row), tmp_path)
    assert any("not found" in error for error in errors)


def test_checker_rejects_unknown_axes_and_invariants() -> None:
    row = "| INV-9 | C9 | ✅ | `tests/workflows/test_output_validation_view.py` |"
    errors = validate_grid(_grid(row), PROJECT_ROOT)
    assert any("unknown invariant" in error for error in errors)
    assert any("outside the legend" in error for error in errors)
