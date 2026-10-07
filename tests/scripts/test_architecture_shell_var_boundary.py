"""Tests for the shell bare-variable / non-ASCII boundary guard (#985).

scripts/architecture/shell_var_boundary.py: a bare ``$NAME`` immediately
followed by a non-ASCII byte breaks under macOS bash 3.2 + UTF-8 locale (the
multibyte lead byte is merged into the variable name), so tracked shell
sources must write ``${NAME}`` there.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

from scripts.architecture.repository import check_repository
from scripts.architecture.shell_var_boundary import (
    check_paths,
    check_shell_var_boundary,
    find_violations,
    is_shell_source,
)

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_BASH = Path("/bin/bash")


def _bytes(text: str) -> bytes:
    return text.encode("utf-8")


@pytest.mark.parametrize(
    "line",
    [
        'echo "已绑定 $BACKEND_BIND，但"',
        'echo "$WORKER_PORT（127.0.0.1）"',
        'echo "$x中"',
        "echo $_v→",
    ],
)
def test_bare_var_before_non_ascii_is_flagged(line: str) -> None:
    errors = find_violations("scripts/x.sh", _bytes(line))
    assert len(errors) == 1
    assert errors[0].startswith("scripts/x.sh:1: bare $")
    assert "#985" in errors[0]


@pytest.mark.parametrize(
    "line",
    [
        'echo "已绑定 ${BACKEND_BIND}，但"',
        'echo "$WORKER_PORT (ascii)"',
        'echo "$1中"',  # positional params take a single digit: unaffected
        'echo "$?：rc"',
        'echo "${rc:-0}（默认）"',
        "  # 注释里的 $VAR，不会被展开",
        "echo plain 中文",
    ],
)
def test_safe_forms_are_not_flagged(line: str) -> None:
    assert find_violations("scripts/x.sh", _bytes(line)) == []


@pytest.mark.parametrize(
    "content",
    [
        "echo '$VAR中文'",  # single quotes never expand
        "echo \\$VAR中文",  # escaped dollar is a literal
        'echo "\\$VAR中文"',  # escaped inside double quotes too
        "echo $'$VAR中文'",  # ANSI-C quoting does not expand $NAME
        "trap 'rm -f \"$TMP，\"' EXIT",  # double quotes nested in single
        "cat <<'EOF'\n$VAR中文\nEOF\n",  # quoted heredoc body is literal
        'cat <<"EOF"\n$VAR中文\nEOF\n',
        "cat <<\\EOF\n$VAR中文\nEOF\n",
        "cat <<EOF\n\\$VAR中文\nEOF\n",  # escaped in unquoted heredoc
        "echo $$X中",  # $$ is the PID parameter, X中 is literal text
        "echo a # 行尾注释 $VAR，",
        "x=$(( 1 << 2 ))\necho ok $X\n",  # arithmetic shift, not a heredoc
        'grep -q x <<<"$S"\necho "ok"',  # here-string is not a heredoc
        # Shifts by a variable inside arithmetic are not heredocs (#1060 review).
        "x=$((1 << SHIFT))\necho '$X，'\n",
        "((x << shift))\necho '$X，'\n",
        "((16#ff))\necho '$X，'\n",  # base prefix, not a comment
    ],
)
def test_non_expanding_contexts_are_not_flagged(content: str) -> None:
    """#1022：只在 shell 会展开的上下文判定——字面量与转义不误报。"""
    assert find_violations("scripts/x.sh", _bytes(content)) == []


@pytest.mark.parametrize(
    ("content", "lineno"),
    [
        # A physical line starting with # inside a multi-line "..." expands.
        ('echo "first\n# $VAR，still quoted"\n', 2),
        # Unquoted heredoc body: # lines and quotes are plain body text.
        ("cat <<EOF\n# $VAR，in heredoc\nEOF\n", 2),
        ("cat <<-EOF\n\t'$VAR，'\n\tEOF\necho done\n", 2),
        # Quotes inside a command substitution open their own frame.
        ('printf \'%s\' "$(printf \'%s\' "$1" | sed "s/\'/x/")"\necho "$Y，"\n', 2),
        # A heredoc after a quoted one is still tracked line by line.
        ("cat <<'A'\n$X，\nA\ncat <<B\n$Z，\nB\n", 5),
        # ANSI-C / locale quoted delimiters name EOF, not $EOF (#1060 review).
        ("cat <<$'EOF'\n$X，\nEOF\necho \"$Y，\"\n", 4),
        ('cat <<$"EOF"\n$X，\nEOF\necho "$Y，"\n', 4),
        # An escaped blank keeps # inside the current word: not a comment.
        ('echo foo\\ # "$X，"\n', 1),
        ("echo a\\ #$X，\n", 1),
        # A # right after a multi-line quote closes still belongs to the word.
        ("echo 'a\nb'# \"$X，\"\n", 2),
        ('echo "a\nb"# "$X，"\n', 2),
        ("echo $'a\nb'# \"$X，\"\n", 2),
        ("x=$(echo 'a\nb'# \"$X，\"\n)\n", 2),
        # A # right after $(…) / $((…)) still belongs to the word (#1060 review).
        ('echo $(printf foo)# "$X，"\n', 1),
        ('echo $((1 << S))# "$X，"\n', 1),
        ('echo "$((1 << S))" "$Y，"\n', 1),
        # $((…)) expands $X even inside single quotes (bash: arithmetic text).
        ("x=$((cd d; cat <<EOF\n'$X，'\nEOF\n))\n", 2),
        # A single ) means bash reparses $(( / (( as nested subshells: the
        # heredoc is real, so fail closed instead of skipping << as a shift.
        ("x=$((cat <<EOF\n'$X，'\nEOF\n) )\n", 2),
        ("((a)\ncat <<EOF\n'$X，'\nEOF\n)\n", 3),
    ],
)
def test_expanding_multiline_contexts_are_flagged(content: str, lineno: int) -> None:
    """#1022：跨行双引号串 / 未加引号 heredoc 内以 # 开头的物理行仍会展开，不漏检。"""
    (error,) = find_violations("scripts/x.sh", _bytes(content))
    assert error.startswith(f"scripts/x.sh:{lineno}: bare $")


@pytest.mark.parametrize(
    ("content", "lineno"),
    [
        ('echo \'unclosed\necho "$X，"\n', 2),  # single quote never closes
        ("cat <<'EOF'\nbody\necho \"$X，\"\n", 3),  # quoted heredoc never terminates
        ('echo "$(printf x\necho $X，\n', 2),  # command substitution left open
    ],
)
def test_unterminated_context_fails_closed(content: str, lineno: int) -> None:
    """#1060 review：引号 / heredoc / 命令替换到文件末尾仍未闭合时回落逐行判定，不整段漏检。"""
    errors = find_violations("scripts/x.sh", _bytes(content))
    assert [e.split(" ")[0] for e in errors] == [f"scripts/x.sh:{lineno}:"]


def test_makefile_recipes_follow_make_semantics() -> None:
    """#1022：Makefile 按 make 语义——注释行跳过；$$ 即 shell 的 $，单个 $ 由 make 展开；
    赋值值经 $(VAR) 粘进 recipe，其中的 $$NAME 仍会成为 shell 的 $NAME。"""
    content = _bytes(
        "# 注释 $$NOPE，\n"
        "MSG = $$PASTED，\n"
        "NAME = $(X)（make 变量）\n"
        "all:\n"
        "\t@echo $(MSG)（make 展开）\n"
        "\t@echo '$$QUOTED，'\n"
        "\t@echo $$STATE_COPY（dev）\n"
        "\t@echo first \\\n"
        "\t  $$CONT，\n"
    )
    errors = find_violations("Makefile", content)
    assert [e.split(" ")[0] + " " + e.split(" ")[2] for e in errors] == [
        "Makefile:2: $PASTED",
        "Makefile:7: $STATE_COPY",
        "Makefile:9: $CONT",
    ]


def test_violation_reports_line_number_and_braced_fix() -> None:
    content = _bytes('#!/bin/bash\nset -u\necho "ok"\necho "$X，"\n')
    (error,) = find_violations("scripts/y.sh", content)
    assert error.startswith("scripts/y.sh:4: bare $X ")
    assert "${X}" in error


def test_every_offending_var_on_a_line_is_reported() -> None:
    line = 'echo "$A，then http://$H:$P（x）"'
    errors = find_violations("scripts/z.sh", _bytes(line))
    assert [e.split(" ")[2] for e in errors] == ["$A", "$P"]


@pytest.mark.parametrize(
    ("path", "head", "expected"),
    [
        ("scripts/a.sh", b"", True),
        ("lib/b.bash", b"", True),
        ("Makefile", b"", True),
        ("make/rules.mk", b"", True),
        (".githooks/pre-push", b"", True),
        ("scripts/git-hooks/pre-commit", b"", True),
        ("bin/tool", b"#!/usr/bin/env bash\n", True),
        ("bin/tool", b"#!/bin/sh\n", True),
        ("bin/tool", b"#!/usr/bin/env python3\n", False),
        ("docs/guide.md", b"", False),
        ("server/app/main.py", b"", False),
    ],
)
def test_shell_source_detection(path: str, head: bytes, expected: bool) -> None:
    assert is_shell_source(path, head) is expected


def test_check_paths_only_scans_shell_sources(tmp_path: Path) -> None:
    (tmp_path / "a.sh").write_bytes(_bytes('echo "$X，"\n'))
    (tmp_path / "notes.md").write_bytes(_bytes("$HOME，文档不执行\n"))
    errors = check_paths(tmp_path, ["a.sh", "notes.md", "missing.sh"])
    assert len(errors) == 1
    assert errors[0].startswith("a.sh:1:")


def test_repo_tracked_shell_sources_are_clean() -> None:
    """修复后的仓库基线：tracked shell 源中无裸变量紧跟非 ASCII（#985 回归钉）。"""
    assert check_shell_var_boundary(REPO_ROOT) == []


def test_check_is_wired_into_repository_gate() -> None:
    assert "check_shell_var_boundary(root)" in inspect.getsource(check_repository)


def test_check_skips_gracefully_without_git(tmp_path: Path) -> None:
    assert check_shell_var_boundary(tmp_path) == []


def _system_bash_is_3x() -> bool:
    if not SYSTEM_BASH.exists():
        return False
    out = subprocess.run(
        [str(SYSTEM_BASH), "-c", "echo ${BASH_VERSINFO[0]}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return out == "3"


@pytest.mark.skipif(not _system_bash_is_3x(), reason="needs macOS system bash 3.2")
def test_bash32_merges_multibyte_lead_byte_and_braces_fix_it() -> None:
    """实测钉：bash 3.2 + UTF-8 locale 下裸 $X 紧跟全角字符触发 unbound variable，
    ${X} 形式正常——即本守卫要防的真实故障。"""
    env = {"LC_ALL": "en_US.UTF-8", "PATH": "/usr/bin:/bin"}
    bare = subprocess.run(
        [str(SYSTEM_BASH), "-c", 'set -u; X=1; echo "$X，"'],
        capture_output=True,
        env=env,
        check=False,
    )
    assert bare.returncode != 0
    assert b"unbound variable" in bare.stderr
    braced = subprocess.run(
        [str(SYSTEM_BASH), "-c", 'set -u; X=1; echo "${X}，"'],
        capture_output=True,
        env=env,
        check=False,
    )
    assert braced.returncode == 0
    assert braced.stdout == _bytes("1，\n")
