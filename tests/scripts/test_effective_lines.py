"""Tests for scripts.architecture.effective_lines."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.architecture.effective_lines import count_effective_lines

pytestmark = pytest.mark.no_db


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_python_excludes_comment_only_and_blank_lines(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "example.py",
        "# module header\n\nimport os  # trailing comment counts\n  # indented comment\nx = 1\n",
    )
    assert count_effective_lines(path) == 2


def test_python_docstring_rows_are_free(tmp_path: Path) -> None:
    # #610: docstrings are documentation, not code — the same courtesy the
    # C-like scanner already extends to JSDoc/Rust doc comments. Fitting a
    # budget must not reward deleting them.
    path = _write(
        tmp_path / "example.py",
        '"""Module docstring.\n\nMore detail.\n"""\nx = 1\n',
    )
    assert count_effective_lines(path) == 1


def test_python_function_and_class_docstrings_free_mixed_rows_count(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "example.py",
        "def f():\n"
        '    """Doc.\n'
        "    Continued.\n"
        '    """  # trailing comment on the closing row stays free\n'
        "    return 1\n"
        "class C:\n"
        '    """Class doc."""\n'
        "    x = 1\n"
        # A one-line def whose body is the docstring: the row carries the
        # def, so it counts (the trailing-comment discipline).
        'def g(): """doc"""\n'
        "return 2\n",
    )
    # counted: def f, return 1, class C, x = 1, def g (docstring on its row),
    # return 2 = 6
    assert count_effective_lines(path) == 6


def test_python_orphan_strings_and_fstrings_are_code(tmp_path: Path) -> None:
    # Later orphan string expressions are values, not documentation; an
    # f-string first statement is not a docstring (ast.get_docstring skips
    # it) — both stay counted.
    path = _write(
        tmp_path / "example.py",
        'def f():\n    f"""not a docstring {1}"""\n    "orphan string"\n    return 1\n',
    )
    assert count_effective_lines(path) == 4


def test_python_hash_inside_string_is_not_a_comment(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "example.py",
        's = """\n# not a comment\n"""\n',
    )
    assert count_effective_lines(path) == 3


def test_python_unparseable_falls_back_to_raw_count(tmp_path: Path) -> None:
    path = _write(tmp_path / "broken.py", "def broken(:\n# comment\n\n")
    assert count_effective_lines(path) == 3


def test_ts_excludes_line_and_block_comments(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "example.ts",
        "// header\n"
        "\n"
        "const a = 1;\n"
        "/* single line block */\n"
        "/* multi\n"
        "   line\n"
        "   block */\n"
        "const b = 2; // trailing\n"
        "/* opens\n"
        "   closes */ const c = 3;\n",
    )
    assert count_effective_lines(path) == 3


def test_ts_comment_markers_inside_strings_count_as_code(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "example.ts",
        'const url = "https://example.com";\n'
        'const marker = "/* not a comment */";\n'
        "const tpl = `// not a comment`;\n",
    )
    assert count_effective_lines(path) == 3


def test_css_block_comments_excluded(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "example.css",
        "/* reset */\n.a {\n  color: red; /* inline */\n}\n",
    )
    assert count_effective_lines(path) == 3


def test_rust_excludes_comments_counts_attribute_macros(tmp_path: Path) -> None:
    """#202：.rs 走 C-like 解析——// 与 /* */ 注释排除，#[derive] 等属性行算代码。"""
    path = _write(
        tmp_path / "example.rs",
        "// header\n"
        "//! doc comment\n"
        "\n"
        "/// doc\n"
        "#[derive(Default)]\n"
        "pub struct S {\n"
        "    a: u32,\n"
        "}\n"
        "/* multi\n"
        "   line */\n"
        "fn f() -> u32 { 1 } // trailing\n",
    )
    # code lines: derive, struct, a, }, fn = 5（doc 与块注释排除）
    assert count_effective_lines(path) == 5


def test_unknown_extension_falls_back_to_raw_count(tmp_path: Path) -> None:
    path = _write(tmp_path / "example.md", "# title\n\ntext\n")
    assert count_effective_lines(path) == 3


def test_sql_excludes_dash_dash_comment_lines(tmp_path: Path) -> None:
    # #293: the schema file's governance counts effective lines like code
    # files — fitting a budget must not reward deleting documentation.
    path = _write(
        tmp_path / "schema.sql",
        "-- header comment\n"
        "\n"
        "create table jobs (\n"
        "  id text primary key -- trailing comment still counts\n"
        ");\n"
        "-- retired note (schema v64)\n",
    )
    # code lines: create, id, ); = 3
    assert count_effective_lines(path) == 3
