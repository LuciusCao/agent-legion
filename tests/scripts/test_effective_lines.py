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


def test_python_docstring_counts_as_code(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "example.py",
        '"""Module docstring.\n\nMore detail.\n"""\nx = 1\n',
    )
    # Blank lines are excluded even inside a docstring; the rest count as code.
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


def test_unknown_extension_falls_back_to_raw_count(tmp_path: Path) -> None:
    path = _write(tmp_path / "example.md", "# title\n\ntext\n")
    assert count_effective_lines(path) == 3
