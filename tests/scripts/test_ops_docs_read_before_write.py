"""运维文档示例与 ensure-s3-bucket.py 的「读成功再写」守卫（#1099 G2 review、#1111、#1115、#1250、#1252）。

codex 在 materials-storage-deployment.md 的 prod CORS 合并脚本上发现：
`get_bucket_cors` 的任意 ClientError 都被当成「还没有 CORS」，随后
`put_bucket_cors` 用只含新 origin 的规则**整份替换**现有配置——读失败
（权限、限流、凭据）就静默删掉生产 origin。同类模式在运维文档示例里不止
一处：重跑即覆盖已有密钥、生成失败把空值写进去、读不到 .env 却整份重写、
下载失败用残缺字节覆盖已有文件、未预览就执行删除。

判定是语义级的：Python 片段用 ast 解析 except 子句（注释与字符串里的
「raise」不算数），shell 片段用容忍引号 / test 写法 / flag 位置 / 变量名等
等价改写的宽松正则。写入一律由同一个判定器识别（#1115）：任意 `>` / `>>` /
`tee` / `--output` / `-o` 指向的目标；「最终目标」= `mv` 的目的地（加上各守卫
声明的受保护路径），直写最终目标即违规——不再按命令名（echo / openssl）或
标记字符串整块跳过。每个判定器另有一组「变异必须红、等价改写必须绿」的自检
用例，防止判定器本身退化成字面量比对。纯静态，不碰 DB。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
_FENCE = re.compile(r"^[ \t]*```(.*)$")
MATERIALS = "docs/materials-storage-deployment.md"
WORKER = "docs/agent-worker-deployment.md"
API_TOKENS = "docs/workspace-api-tokens.md"
OPS_DOCS = (MATERIALS, WORKER, "docs/remote-execution-runbook.md", API_TOKENS)
_MISSING_CORS = "NoSuchCORSConfiguration"
# 「bucket 没有 CORS 配置」的错误码白名单。RustFS 1.0.0-beta.12 同样返回
# NoSuchCORSConfiguration（源码核对，#1115）；新增后端等价码时加在这里。
_MISSING_CORS_CODES = frozenset({_MISSING_CORS})


def _text(doc: str) -> str:
    return (ROOT / doc).read_text(encoding="utf-8")


def _blocks(doc: str) -> list[tuple[str, str]]:
    """(语言, 代码) 列表；fence 必须配平。"""
    blocks: list[tuple[str, str]] = []
    lang: str | None = None
    current: list[str] = []
    for line in _text(doc).splitlines():
        match = _FENCE.match(line)
        if match:
            if lang is None:
                lang, current = match.group(1).strip(), []
            else:
                blocks.append((lang, "\n".join(current)))
                lang = None
        elif lang is not None:
            current.append(line)
    assert lang is None, f"{doc}: 代码块 fence 未配平"
    return blocks


def _python_cors_body(block: str) -> str:
    """文档里的 CORS 片段是 `… python - <<'EOF'` heredoc 包着的 Python。"""
    lines = block.splitlines()
    end = next(i for i, line in enumerate(lines) if line.strip() == "EOF")
    start = max(i for i in range(end) if "<<'EOF'" in lines[i])
    return "\n".join(lines[start + 1 : end])


# —— 判定器：CORS 读失败（Python ast） ——————————————————————————


def _is_client_error(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "ClientError"
    if isinstance(node, ast.Attribute):
        return node.attr == "ClientError"
    return False


def _root_name(node: ast.expr) -> str | None:
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            return None


def _is_code_constant(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == "Code"


def _is_error_code(node: ast.expr, exc_name: str | None, code_vars: set[str]) -> bool:
    """node 是错误码**字符串**：从捕获的异常上取 `…["Code"]` / `….get("Code")`，
    或由这类表达式赋值的变量。拿异常对象本身（`exc != "…"`）、`str(exc)`、
    Message 字段等一律不算——它们和错误码比较恒不等，等于吞掉全部读失败。"""
    if isinstance(node, ast.Name):
        return node.id in code_vars
    if isinstance(node, ast.Subscript):
        picks_code = _is_code_constant(node.slice)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        picks_code = node.func.attr == "get" and bool(node.args) and _is_code_constant(node.args[0])
    else:
        return False
    return picks_code and exc_name is not None and _root_name(node) == exc_name


def _compares_missing_cors(
    test: ast.expr, exc_name: str | None, code_vars: set[str]
) -> tuple[bool, bool]:
    """(是否拿错误码与 NoSuchCORSConfiguration 比对, 是否为否定式 != / not in)。

    `in` / `not in` 接受常量元组 / 集合 / 列表，便于日后把其它后端的等价错误码
    并入放行集合——但集合里**每一项**都必须是白名单内的「配置不存在」码：混进
    AccessDenied 之类就等于对它也继续往下覆盖（#1213 codex）。"""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False, False
    op, left, right = test.ops[0], test.left, test.comparators[0]
    if isinstance(op, (ast.Eq, ast.NotEq)):
        if isinstance(left, ast.Constant):
            left, right = right, left
        hit = (
            isinstance(right, ast.Constant)
            and right.value in _MISSING_CORS_CODES
            and _is_error_code(left, exc_name, code_vars)
        )
        return hit, isinstance(op, ast.NotEq)
    if isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, (ast.Tuple, ast.Set, ast.List)):
        hit = (
            bool(right.elts)
            and all(
                isinstance(item, ast.Constant) and item.value in _MISSING_CORS_CODES
                for item in right.elts
            )
            and _is_error_code(left, exc_name, code_vars)
        )
        return hit, isinstance(op, ast.NotIn)
    return False, False


def _raises(statements: list[ast.stmt]) -> bool:
    return any(isinstance(stmt, ast.Raise) for stmt in statements)


def _code_vars(handler: ast.ExceptHandler) -> set[str]:
    names: set[str] = set()
    for stmt in handler.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and _is_error_code(stmt.value, handler.name, names)
        ):
            names.add(stmt.targets[0].id)
    return names


def cors_read_violations(source: str) -> list[str]:
    """每个包住 get_bucket_cors 的 try：只能捕获 ClientError，且只在错误码为
    NoSuchCORSConfiguration 时继续，其余分支必须 raise。"""
    tree = ast.parse(source)
    found = False
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if "get_bucket_cors" not in ast.unparse(ast.Module(body=node.body, type_ignores=[])):
            continue
        found = True
        for handler in node.handlers:
            if not _is_client_error(handler.type):
                problems.append(f"except {ast.unparse(handler.type) if handler.type else ''}: 过宽")
                continue
            code_vars = _code_vars(handler)
            guarded = False
            for stmt in handler.body:
                if not isinstance(stmt, ast.If):
                    continue
                hit, negated = _compares_missing_cors(stmt.test, handler.name, code_vars)
                if not hit:
                    continue
                # 否定式：if 体必须 raise；肯定式：else 必须 raise。
                guarded = _raises(stmt.body) if negated else _raises(stmt.orelse)
            if not guarded:
                problems.append("except ClientError 未在错误码非 NoSuchCORSConfiguration 时 raise")
    if not found:
        problems.append("未找到包住 get_bucket_cors 的 try")
    return problems


# —— 判定器：shell 写入（统一识别） ——————————————————————————————

_Q = r"[\"']?"  # 可选引号
_DEST = r"\s*[\"']?(?P<dest>[^\s\"';|&<>()]+)"
# `>` / `>>` / `>|` / `N>` / `&>`；`>&2` 之类 fd 复制与 `>(…)` 进程替换不是写文件。
_REDIRECT = re.compile(r"(?<![<>&])(?:\d|&)?>>?\|?(?![>&(])" + _DEST)
# `--output F` / `--output=F` / `-o F` / `-fsSLo F` 等组合短 flag。
_OUTPUT_FLAG = re.compile(r"(?:--output(?:=|\s+)|(?<![\w-])-[A-Za-z]*o(?=[\s\"'$]))" + _DEST)
_TEE = re.compile(r"\btee\b(?P<args>[^\n|;&<>)]*)")
_MV = re.compile(r"\bmv\s+(?:-\S*\s+)*(\S+)\s+(\S+)")


def _norm(word: str) -> str:
    word = re.sub(r"\$\{(\w+)\}", r"$\1", word.strip("\"';"))
    return word[2:] if word.startswith("./") else word


def write_targets(block: str) -> list[tuple[int, str]]:
    """(位置, 归一化目标)：块里所有写文件的目的地。"""
    found = [(m.start(), m.group("dest")) for m in _REDIRECT.finditer(block)]
    found += [(m.start(), m.group("dest")) for m in _OUTPUT_FLAG.finditer(block)]
    for match in _TEE.finditer(block):
        found += [(match.start(), w) for w in match.group("args").split() if not w.startswith("-")]
    return sorted((pos, _norm(dest)) for pos, dest in found)


def _mv_destinations(block: str) -> set[str]:
    return {_norm(dst) for _, dst in _MV.findall(block)}


def _writes_final(block: str, protected: Callable[[str], bool] = lambda _: False) -> bool:
    """是否直写最终目标（mv 的目的地或受保护路径）——绕过「先写临时文件再改名」。"""
    finals = _mv_destinations(block)
    return any(target in finals or protected(target) for _, target in write_targets(block))


_TEST_E = re.compile(r"(?:\[\[?\s+|test\s+)-e\s+" + _Q + r"\$\{?\w+")
_TEST_S = re.compile(r"(?:\[\[?\s+|test\s+)-s\s+" + _Q + r"\$\{?\w+\}?\.tmp")
_MV_TMP = re.compile(r"\bmv\s+(?:--\s+)?" + _Q + r"\$\{?\w+\}?\.tmp" + _Q + r"\s")


def secret_generation_ok(block: str) -> bool:
    """写密钥的块：不直写 deploy/secrets/ 或改名目标，且有存在检查、非空检查与
    临时文件改名。不写任何文件的块（chmod 等）天然无害。"""
    if not write_targets(block):
        return True
    if _writes_final(block, lambda t: t.startswith("deploy/secrets/")):
        return False
    return bool(_TEST_E.search(block) and _TEST_S.search(block) and _MV_TMP.search(block))


# grep 的 quiet 守卫：-q 可在任意 flag 位置（-qE / -Eq / -E -q），或长 flag。
_GREP_QUIET = re.compile(
    r"\bgrep\b(?=[^\n|;]*?\s(?:-[A-Za-z]*q[A-Za-z]*|--quiet|--silent)(?=[\s\"']|$))"
)
_GREP_INVERT = re.compile(r"\bgrep\b(?=[^\n|;]*?\s(?:-[A-Za-z]*v[A-Za-z]*|--invert-match)(?=\s))")
_S3_KEYS = ("BUCKET", "ENDPOINT", "ACCESS_KEY", "SECRET_KEY")
_INLINE_RANDOM = re.compile(r"^\s*AGENT_LEGION_S3_\w+=\$\(", re.M)
_WRITTEN_VAR = re.compile(r"^\s*AGENT_LEGION_S3_\w+=" + _Q + r"\$\{?(\w+)", re.M)
_TEST_N = re.compile(r"(?:\[\[?\s+|test\s+)-n\s+" + _Q + r"\$\{?(\w+)")


def s3_credentials_ok(block: str) -> bool:
    """首次写 S3 凭据：quiet grep 守卫覆盖全部四个键；随机值先落变量，写进
    .env 的每个变量都判非空（变量名不限）；生成失败的 else 分支显式报错。"""
    guard = next((line for line in block.splitlines() if _GREP_QUIET.search(line)), "")
    written = set(_WRITTEN_VAR.findall(block))
    return (
        all(key in guard for key in _S3_KEYS)
        and not _INLINE_RANDOM.search(block)
        and written <= set(_TEST_N.findall(block))
        and bool(re.search(r"^\s*else\b[\s\S]*>&2", block, re.M))
    )


def _env_target(target: str) -> bool:
    return target == "deploy/.env"


# 追加写入的键：echo/printf 参数与 heredoc 体里的 K= 记号（.env 键为
# UPPER_SNAKE）。提取不到键的直写无法核对守卫覆盖，按违规处理（#1252）。
_WRITTEN_KEY = re.compile(r"\b([A-Z_][A-Z0-9_]*)=")
_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?")
# 读失败区分（#1250）：分支里 `$?` 判 1（无匹配才继续），或对 deploy/.env
# 的否定存在性检查（文件不存在才继续）——「文件存在但读失败」都不再追加。
_RC_IS_ONE = re.compile(r"\$\?[\"']?\s+(?:-eq\s+1|-le\s+1|-lt\s+2)\b")
_NOT_EXISTS_ENV = re.compile(
    r"(?:\[\[?\s+!\s+-[ef]|!\s+\[\[?\s+-[ef])\s+[\"']?(?:\./)?deploy/\.env\b"
)


def _written_env_keys(block: str, pos: int) -> set[str]:
    """pos 处直写 deploy/.env 的键：写入命令的 K= 记号与 heredoc 体的 K= 行。
    命令范围只取最近的分隔符（`;` / 换行 / `&&` / `||`）之后——同一行前面的
    grep 条件里的 `K=` 不算写入。"""
    line_start = block.rfind("\n", 0, pos) + 1
    before = block[line_start:pos]
    command_start = max(before.rfind(";"), before.rfind("&&"), before.rfind("||")) + 1
    line_end = block.find("\n", pos)
    command = before[command_start:] + block[pos : line_end if line_end != -1 else len(block)]
    keys = set(_WRITTEN_KEY.findall(command))
    heredoc = _HEREDOC.search(command)
    if heredoc is not None and line_end != -1:
        for line in block[line_end + 1 :].splitlines():
            if line.strip() == heredoc.group(1):
                break
            key = re.match(r"([A-Z_][A-Z0-9_]*)=", line)
            if key:
                keys.add(key.group(1))
    return keys


def _grep_checked_pattern(block: str, pos: int) -> re.Pattern[str] | None:
    """pos 处 grep 命令检查的键模式（首个引号包裹的参数，ERE 按 Python re 解释）。"""
    command = re.match(_COMMAND, block[pos:])
    if command is None:
        return None
    match = re.search(r"\bgrep\b(?:\s+--?\w[-\w]*)*\s+(['\"])(?P<pattern>.*?)\1", command.group())
    if match is None:
        return None
    try:
        return re.compile(match.group("pattern"))
    except re.error:
        return None


def env_appends_guarded(block: str) -> bool:
    """直写 deploy/.env（任意写法）必须落在针对该文件的 quiet grep 存在性检查的
    「未匹配」分支里，否则重跑重复追加键。只比文本先后不够（#1213 codex）：
    `grep -q K f; echo K=v >> f` 的追加是无条件的。另两项（#1250/#1252）：
    grep 检查的键必须覆盖写入的全部键（查 OTHER 却追加 K 不算数）；分支必须
    区分「无匹配」(rc=1) 与「读失败」(rc=2)——`$?` 判 1 或 `! -e` 存在性检查，
    「文件存在但不可读」时不得盲目追加（文件不存在是首次配置，照常追加）。"""
    writes = [pos for pos, target in write_targets(block) if _env_target(target)]
    if not writes:
        return True
    guards = [
        (match, span)
        for match in _GREP_QUIET.finditer(block)
        if _greps_env(block, match.start())
        for span in _unmatched_branches(block, match.start())
    ]
    for pos in writes:
        keys = _written_env_keys(block, pos)
        if not keys:
            return False
        covered = False
        for match, (start, end) in guards:
            if not (start <= pos < end):
                continue
            pattern = _grep_checked_pattern(block, match.start())
            if pattern is None or not all(pattern.search(f"{key}=") for key in keys):
                continue
            span = block[start:pos]
            if _RC_IS_ONE.search(span) or _NOT_EXISTS_ENV.search(span):
                covered = True
                break
        if not covered:
            return False
    return True


_SHELL_KEYWORD = re.compile(r"(?<![\w$-])(if|then|elif|else|fi)(?![\w-])")


def _unmatched_branches(block: str, grep_pos: int) -> list[tuple[int, int]]:
    """grep 判「无匹配」时才执行的区间：`if grep …; then A else B fi` 的 B（含 elif
    链），`if ! grep …; then A … fi` 的 A，或 `grep … || cmd` 的 cmd。"""
    head = block[:grep_pos]
    if_head = re.search(r"\bif\s+(!\s+)?$", head)
    if if_head is None:
        command_end = re.match(r"[^\n;]*?(?=;|&&|\|\||\n|$)", block[grep_pos:])
        assert command_end is not None
        tail_start = grep_pos + command_end.end()
        or_branch = re.match(r"\|\|[^\n;]*", block[tail_start:])
        return [(tail_start, tail_start + or_branch.end())] if or_branch else []
    # 注释换成等长空白，保持位置不变，避免中文注释里的关键字干扰配对。
    code = re.sub(r"(?m)(^|\s)#.*$", lambda m: m.group(1) + " " * (len(m.group()) - 1), block)
    depth, then_at, else_at = 0, None, None
    for match in _SHELL_KEYWORD.finditer(code, grep_pos):
        word = match.group(1)
        if word == "if":
            depth += 1
        elif word == "fi" and depth:
            depth -= 1
        elif depth == 0 and word == "then" and then_at is None:
            then_at = match.end()
        elif depth == 0 and word in ("elif", "else") and else_at is None:
            else_at = match.start()
        elif depth == 0 and word == "fi":
            if then_at is None:
                return []
            if if_head.group(1):  # if ! grep：then 分支是未匹配
                return [(then_at, else_at if else_at is not None else match.start())]
            return [(else_at, match.start())] if else_at is not None else []
    return []


def _greps_env(block: str, pos: int) -> bool:
    """从 grep 起到本条命令结束（`;` / `&&` / `||` / 换行）是否读的是 deploy/.env。"""
    command = re.match(r"[^\n;]*?(?=;|&&|\|\||\n|$)", block[pos:])
    return command is not None and bool(
        re.search(r"(?:\s|[\"'])(?:\./)?deploy/\.env\b", command.group())
    )


# 中止：`return` / `exit`，或同一行内含 return / exit 的 `{ …; }` 命令组。
_ABORT = r"\s*(?:\{[^\n]*?\b(?:return|exit)\b|(?:return|exit)\b)"


_SEPARATORS = re.compile(r"^(?:[ \t]*(?:\n|;|&&|\|\|)[ \t]*)+")
_COMMAND = r"[^\n;]*?(?=;|&&|\|\||\n|$)"
_RC_CAPTURE = re.compile(r"^(\w+)=\$\?(?=\s|$)")
_RC_IMMEDIATE_TEST = re.compile(r"^(?:\[\[?\s|test\s|\(\(|case\s)")


def _first_command_after(block: str, pos: int) -> str:
    """pos 处的命令结束后紧随的第一条命令文本（跳过分隔符与 &&/|| 链接）。"""
    head = re.match(_COMMAND, block[pos:])
    assert head is not None
    tail = _SEPARATORS.sub("", block[pos + head.end() :])
    end = re.match(_COMMAND, tail)
    assert end is not None
    return tail[: end.end()]


def _grep_rc_checked(block: str, after: int) -> bool:
    """grep 之后区分「无匹配」(1) 与「读失败」(2)，且读失败**必须中止**：比较对象
    是 `$?` 或由 `$?` 赋值的变量，形态为 `≤1 || 中止`、`≥2 && 中止`、
    `if ≥2; then … 中止 … fi`、`case … 0|1) ;; *) 中止`。只识别比较、不看命中
    后控制流时，`[ "$rc" -gt 1 ] && echo warn` 也能过（#1213 codex）。
    `$?` 的捕获或比较必须是紧随 grep 的第一条命令（#1252）：隔一条命令后
    `$?` 已被覆盖，`rc=$?` 存的是别人的退出码。"""
    first = _first_command_after(block, after)
    capture = _RC_CAPTURE.match(first)
    if capture:
        names = [capture.group(1)]
    elif _RC_IMMEDIATE_TEST.match(first) and "$?" in first:
        names = ["\\?"]
    else:
        return False
    rest = block[after:]
    ref = r"[\"']?\$\{?(?:" + "|".join(names) + r")\}?[\"']?"
    arith_ref = r"\$?\{?(?:" + "|".join(names) + r")\}?"

    def test(cmp: str) -> str:
        return (
            r"(?:(?:\[\[?|\btest)\s+" + ref + r"\s+" + cmp + r"\b(?:\s*\]\]?)?"
            r"|\(\(\s*" + arith_ref + r"\s*" + _ARITH[cmp] + r"\s*\)\))"
        )

    patterns = (
        test(_RC_OK) + r"\s*\|\|" + _ABORT,
        test(_RC_FAIL) + r"\s*&&" + _ABORT,
        r"\bif\s+" + test(_RC_FAIL) + r"\s*;?\s*then\b(?:(?!\bfi\b)[\s\S])*?\b(?:return|exit)\b",
        r"\|\|\s*(?:\[\[?|\btest)\s+" + ref + r"\s+-eq\s+1\b(?:\s*\]\]?)?\s*\|\|" + _ABORT,
        r"\bcase\s+" + ref + r"\s+in\b\s*(?:0\|1|1\|0|\[01\])\)\s*;;\s*\*\)"
        r"(?:(?!;;)[\s\S])*?\b(?:return|exit)\b",
    )
    return any(re.search(pattern, rest) for pattern in patterns)


_RC_OK = r"(?:-le\s+1|-lt\s+2)"
_RC_FAIL = r"(?:-gt\s+1|-ge\s+2)"
_ARITH = {_RC_OK: r"(?:<=\s*1|<\s*2)", _RC_FAIL: r"(?:>\s*1|>=\s*2)"}


def env_edit_ok(block: str) -> bool:
    """单键写 .env：不得直写 deploy/.env（echo / printf / cat <<X / tee 皆然），
    不得直写改名目标；替换式改写须区分 grep 的「无匹配」(1) 与「读失败」(2)，
    读失败中止。"""
    if _writes_final(block, _env_target):
        return False
    invert = _GREP_INVERT.search(block)
    if invert:
        return _grep_rc_checked(block, invert.start()) and bool(_MV_TMP.search(block))
    return True


_CP_EXAMPLE = re.compile(r"\bcp\b((?:\s+-\S+)*)\s+\S*example\S*")


def example_copies_ok(text: str) -> bool:
    copies = _CP_EXAMPLE.findall(text)
    return bool(copies) and all(
        re.search(r"(?:^|\s)(?:-\w*n\w*|--no-clobber)\b", f) for f in copies
    )


_MV_PART = re.compile(
    r"\bmv\s+(?:--\s+)?" + _Q + r"\$\{?OUT\}?\.part" + _Q + r"\s+" + _Q + r"\$\{?OUT\}?" + _Q
)


def download_ok(block: str) -> bool:
    """下载先写 $OUT.part、成功才改名：任何写法直写 $OUT 都违规。"""
    return not _writes_final(block, lambda t: t == "$OUT") and bool(_MV_PART.search(block))


def delete_empty_ok(block: str) -> bool:
    """volume.deleteEmpty -apply 之前必须有一次不带 -apply 的预览。"""
    lines = [line for line in block.splitlines() if "volume.deleteEmpty" in line]
    applied = [i for i, line in enumerate(lines) if "-apply" in line]
    previews = [i for i, line in enumerate(lines) if "-apply" not in line]
    return bool(applied) and bool(previews) and min(previews) < min(applied)


def sync_note_ok(text: str) -> bool:
    for match in re.finditer(r"aws s3 sync", text):
        window = text[match.start() : match.start() + 300]
        if "--dryrun" not in window or "--delete" not in window:
            return False
    return "aws s3 sync" in text


# —— 判定器自检：变异必须红，等价改写必须绿 ————————————————————

_CORS_OK = """
try:
    rules = c.get_bucket_cors(Bucket=b)["CORSRules"]
except ClientError as exc:
    if exc.response.get("Error", {}).get("Code") != "NoSuchCORSConfiguration":
        raise
    rules = []
"""
_CORS_EQUIVALENTS = [
    """
try:
    rules = c.get_bucket_cors(Bucket=b)["CORSRules"]
except botocore.exceptions.ClientError as err:
    code = err.response["Error"]["Code"]
    if "NoSuchCORSConfiguration" == code:
        rules = []
    else:
        raise
""",
    # 放行集合形态（日后并入其它后端的等价错误码）
    """
try:
    rules = c.get_bucket_cors(Bucket=b)["CORSRules"]
except ClientError as exc:
    if exc.response["Error"].get("Code", "") not in {"NoSuchCORSConfiguration"}:
        raise
    rules = []
""",
]
_CORS_MUTANTS = {
    "bare_swallow": _CORS_OK.replace(
        'if exc.response.get("Error", {}).get("Code") != "NoSuchCORSConfiguration":\n        raise\n',
        "",
    ),
    "pass_comment_raise": _CORS_OK.replace("        raise", "        pass  # raise"),
    "except_exception": _CORS_OK.replace("except ClientError as exc", "except Exception as exc"),
    "bare_except": _CORS_OK.replace("except ClientError as exc", "except"),
    "wrong_code": _CORS_OK.replace("NoSuchCORSConfiguration", "AccessDenied"),
    "string_raise": _CORS_OK.replace("        raise", '        msg = "raise"'),
    # 比异常对象 / 其字符串 / Message 字段：恒不等，读失败全被吞（#1115）
    "compare_exc_object": _CORS_OK.replace('exc.response.get("Error", {}).get("Code")', "exc"),
    "compare_str_exc": _CORS_OK.replace('exc.response.get("Error", {}).get("Code")', "str(exc)"),
    "compare_message": _CORS_OK.replace('get("Code")', 'get("Message")'),
    "compare_other_root": _CORS_OK.replace("exc.response.get", "other.response.get"),
    # 放行集合混进非「配置不存在」码：权限错误也继续往下覆盖（#1213 codex）
    "allowlist_with_access_denied": _CORS_EQUIVALENTS[1].replace(
        '{"NoSuchCORSConfiguration"}', '{"NoSuchCORSConfiguration", "AccessDenied"}'
    ),
    "positive_set_with_access_denied": _CORS_EQUIVALENTS[0].replace(
        '"NoSuchCORSConfiguration" == code', 'code in ("NoSuchCORSConfiguration", "AccessDenied")'
    ),
    "empty_allowlist": _CORS_EQUIVALENTS[1].replace('{"NoSuchCORSConfiguration"}', "()"),
}

_SECRET_GOOD = (
    'if [ -e "$target" ]; then :; fi\n"$@" > "$target.tmp" && [ -s "$target.tmp" ]'
    ' && mv "$target.tmp" "$target"'
)
_S3_GOOD = (
    "if grep -qE '^AGENT_LEGION_S3_(BUCKET|ENDPOINT|ACCESS_KEY|SECRET_KEY)=' f; then\n"
    ':\nelif A=$(x) && SECRET_KEY=$(y) && [ -n "$SECRET_KEY" ]; then\n'
    "cat >> f <<EOF\nAGENT_LEGION_S3_SECRET_KEY=$SECRET_KEY\nEOF\nelse\n  echo bad >&2\nfi"
)
_ENV_GOOD = 'grep -v "^$2=" "$1" > "$1.tmp"\n[ $? -le 1 ] || return 1\nmv "$1.tmp" "$1"'
_DOWNLOAD_GOOD = 'curl -o "$OUT.part" u\nmv "$OUT.part" "$OUT"'


@pytest.mark.parametrize(
    ("check", "good", "equivalents", "mutants"),
    [
        pytest.param(
            lambda s: not cors_read_violations(s),
            _CORS_OK,
            _CORS_EQUIVALENTS,
            list(_CORS_MUTANTS.values()),
            id="cors",
        ),
        pytest.param(
            secret_generation_ok,
            _SECRET_GOOD,
            [
                "if test -e $t; then :; fi\ncmd > $t.tmp && test -s $t.tmp && mv -- $t.tmp $t",
                "if [ -e '${t}' ]; then :; fi\ncmd > ${t}.tmp && [ -s ${t}.tmp ] && mv ${t}.tmp ${t}",
                _SECRET_GOOD.replace('"$@" > "$target.tmp"', '"$@" | tee "$target.tmp" >/dev/null'),
                "chmod 600 deploy/secrets/postgres_password",  # 不写文件的块
            ],
            [
                "openssl rand -hex 32 > deploy/secrets/postgres_password",
                "openssl rand -hex 32 | tee deploy/secrets/postgres_password",
                "openssl rand -hex 32 | tee -a ./deploy/secrets/x",
                # 不含 openssl 也照判（#1115：原先整块跳过）
                "uv run python -c 'gen()' > deploy/secrets/vault_master_key",
                # 函数内直写改名目标（#1115）
                _SECRET_GOOD.replace('"$@" > "$target.tmp"', '"$@" > "$target"'),
                'cmd > "$target.tmp" && mv "$target.tmp" "$target"',  # 缺存在检查与非空检查
                'if [ -e "$t" ]; then :; fi\ncmd > "$t.tmp" && mv "$t.tmp" "$t"',  # 缺非空检查
            ],
            id="secrets",
        ),
        pytest.param(
            s3_credentials_ok,
            _S3_GOOD,
            [
                "if grep -Eq '^AGENT_LEGION_S3_(BUCKET|ENDPOINT|ACCESS_KEY|SECRET_KEY)=' f; then\n"
                ":\nelif SECRET_KEY=$(y) && test -n $SECRET_KEY; then\n:\nelse\n  echo bad >&2\nfi",
                # flag 位置与长 flag（#1115）
                _S3_GOOD.replace("grep -qE", "grep -E -q"),
                _S3_GOOD.replace("grep -qE", "grep --quiet -E"),
                # 变量名不限（#1115）
                _S3_GOOD.replace("SECRET_KEY=$(y)", "SK=$(y)")
                .replace('"$SECRET_KEY"', '"$SK"')
                .replace("=$SECRET_KEY\n", "=${SK}\n"),
            ],
            [
                # 只看 ACCESS_KEY：已配置 endpoint / bucket 的外部 S3 仍会被追加
                "if grep -q '^AGENT_LEGION_S3_ACCESS_KEY=' f; then\n:\n"
                'elif SECRET_KEY=$(y) && [ -n "$SECRET_KEY" ]; then\n:\nelse\n echo bad >&2\nfi',
                # heredoc 里现场生成：失败照样写空值
                "if grep -qE '^AGENT_LEGION_S3_(BUCKET|ENDPOINT|ACCESS_KEY|SECRET_KEY)=' f; then\n"
                ":\nelse\ncat >> f <<EOF\nAGENT_LEGION_S3_SECRET_KEY=$(openssl rand -hex 40)\nEOF\n"
                " echo x >&2\nfi",
                # 生成失败无显式报错
                "if grep -qE '^AGENT_LEGION_S3_(BUCKET|ENDPOINT|ACCESS_KEY|SECRET_KEY)=' f; then\n"
                ':\nelif SECRET_KEY=$(y) && [ -n "$SECRET_KEY" ]; then\n:\nfi',
                # 写进 .env 的变量没判非空
                _S3_GOOD.replace(' && [ -n "$SECRET_KEY" ]', ""),
                _S3_GOOD.replace('[ -n "$SECRET_KEY" ]', '[ -n "$A" ]'),
                # 不是 quiet 守卫（-v / -c 会输出且语义不同）
                _S3_GOOD.replace("grep -qE", "grep -E"),
            ],
            id="s3-credentials",
        ),
        pytest.param(
            env_edit_ok,
            _ENV_GOOD,
            [
                "grep -v ^$2= $1 > $1.tmp\n[ $? -le 1 ] || exit 1\nmv -- $1.tmp $1",
                # rc 检查的等价写法（#1115）
                _ENV_GOOD.replace("[ $? -le 1 ]", "test $? -lt 2"),
                _ENV_GOOD.replace("[ $? -le 1 ] || return 1", 'rc=$?\n[ "$rc" -gt 1 ] && return 1'),
                _ENV_GOOD.replace("[ $? -le 1 ] || return 1", "(( $? <= 1 )) || return 1"),
                _ENV_GOOD.replace('"$1.tmp"\n[ $? -le 1 ]', '"$1.tmp" || [ $? -eq 1 ]'),
                _ENV_GOOD.replace(
                    "[ $? -le 1 ] || return 1", "case $? in 0|1) ;; *) return 1;; esac"
                ),
                _ENV_GOOD.replace("grep -v", "grep -Ev"),
                _ENV_GOOD.replace(
                    "[ $? -le 1 ] || return 1",
                    'rc=$?\nif [ "$rc" -ge 2 ]; then\n  echo x >&2\n  return 1\nfi',
                ),
                _ENV_GOOD.replace("return 1", '{ rm -f "$1.tmp"; echo x >&2; return 1; }'),
            ],
            [
                "echo 'K=v' >> deploy/.env",
                'echo "K=v" >> "./deploy/.env"',
                # printf / cat heredoc / tee 同样是直写（#1115）
                "printf '%s\\n' K=v >> deploy/.env",
                "cat >> deploy/.env <<X\nK=v\nX",
                "echo K=v | tee -a deploy/.env",
                'grep -v "^$2=" "$1" > "$1.tmp"\nmv "$1.tmp" "$1"',  # 读失败不中止
                # rc 捕获没有紧随 grep：rc 里存的是 echo 的退出码（#1252）
                'grep -v "^$2=" "$1" > "$1.tmp"\necho logged\nrc=$?\n[ "$rc" -gt 1 ] && return 1\nmv "$1.tmp" "$1"',
                _ENV_GOOD.replace("[ $? -le 1 ]", "[ $? -le 2 ]"),  # 读失败也继续
                _ENV_GOOD.replace('> "$1.tmp"\n', '> "$1"\n'),  # 直写改名目标
                # 比较对了但命中后不中止，mv 照样把清空的临时文件盖回去（#1213 codex）
                _ENV_GOOD.replace(
                    "[ $? -le 1 ] || return 1", 'rc=$?\n[ "$rc" -gt 1 ] && echo warning >&2'
                ),
                _ENV_GOOD.replace("return 1", "echo warn >&2"),
                _ENV_GOOD.replace("return 1", "{ echo warn >&2; }"),
                _ENV_GOOD.replace(" || return 1", ""),
                _ENV_GOOD.replace(
                    "[ $? -le 1 ] || return 1", "case $? in 0|1) ;; *) echo warn;; esac"
                ),
                _ENV_GOOD.replace(
                    "[ $? -le 1 ] || return 1", "if [ $? -gt 1 ]; then echo warn; fi"
                ),
            ],
            id="env-edit",
        ),
        pytest.param(
            env_appends_guarded,
            "if grep -q '^K=' deploy/.env; then :; elif [ $? -eq 1 ]; then echo K=v >> deploy/.env; fi",
            [
                "set_env deploy/.env K v",
                "if grep -E --quiet '^K=' ./deploy/.env; then :\nelif [ $? -eq 1 ]; then\n printf K=v >> ./deploy/.env\nfi",
                # 文件不存在（首次配置）也走追加：rc=1 或 ! -e 任一（#1250）
                "if grep -q '^K=' deploy/.env; then :\nelif [ $? -eq 1 ] || [ ! -e deploy/.env ]; then\n echo K=v >> deploy/.env\nelse\n echo bad >&2\nfi",
                "if grep -q '^K=' deploy/.env; then :; elif [ ! -e deploy/.env ]; then echo K=v >> deploy/.env; fi",
                # elif 链与嵌套 if 都属于未匹配分支
                "if grep -q '^K=' deploy/.env; then :\nelif [ $? -eq 1 ] && V=$(x); then\n"
                '  if [ -n "$V" ]; then :; fi\n  echo K=$V >> deploy/.env\nfi',
                # heredoc 多键写入，grep 模式覆盖全部键（materials 文档的 S3 形态）
                "if grep -qE '^AGENT_LEGION_S3_(BUCKET|SECRET_KEY)=' deploy/.env; then :\n"
                "elif [ $? -eq 1 ] || [ ! -e deploy/.env ]; then\n"
                "cat >> deploy/.env <<EOF\nAGENT_LEGION_S3_BUCKET=b\nAGENT_LEGION_S3_SECRET_KEY=$SK\nEOF\nfi",
            ],
            [
                "echo K=v >> deploy/.env",
                "cat >> deploy/.env <<X\nK=v\nX",
                "echo K=v >> deploy/.env\ngrep -q '^K=' deploy/.env",  # 守卫在写之后
                "if grep -q '^K=' other.env; then :; else echo K=v >> deploy/.env; fi",
                # 守卫在前但追加不受它控制（#1213 codex）
                "grep -q '^K=' deploy/.env; echo K=v >> deploy/.env",
                "grep -q '^K=' deploy/.env && echo K=v >> deploy/.env",
                "if grep -q '^K=' deploy/.env; then echo K=v >> deploy/.env; fi",
                "if grep -q '^K=' deploy/.env; then :; fi\necho K=v >> deploy/.env",
                "if ! grep -q '^K=' deploy/.env; then :; else echo K=v >> deploy/.env; fi",
                "if grep -q '^K=' deploy/.env; then :; else :; fi; echo K=v >> deploy/.env",
                # 不区分无匹配与读失败（#1250）：文件存在但不可读会盲目追加
                "if grep -q '^K=' deploy/.env; then :; else echo K=v >> deploy/.env; fi",
                "if ! grep -q '^K=' deploy/.env; then echo K=v >> deploy/.env; fi",
                "grep -q '^K=' deploy/.env || echo K=v >> deploy/.env",
                # 守卫的键与写入的键不一致（#1252）：OTHER 不存在时每次重跑都重复追加 K
                "if grep -q '^OTHER=' deploy/.env; then :; elif [ $? -eq 1 ]; then echo K=v >> deploy/.env; fi",
                # heredoc 写入了守卫模式未覆盖的键（#1252）
                "if grep -qE '^AGENT_LEGION_S3_(BUCKET|SECRET_KEY)=' deploy/.env; then :\n"
                "elif [ $? -eq 1 ]; then\n"
                "cat >> deploy/.env <<EOF\nAGENT_LEGION_S3_BUCKET=b\nOTHER_KEY=x\nEOF\nfi",
                # 提取不到写入键（动态值）：无法核对守卫覆盖，按违规处理
                "if grep -q '^K=' deploy/.env; then :; elif [ $? -eq 1 ]; then echo \"$V\" >> deploy/.env; fi",
            ],
            id="env-append",
        ),
        pytest.param(
            example_copies_ok,
            "cp -n a.example b",
            ["cp --no-clobber deploy/x.example.yaml deploy/y.yaml", "cp -nv a.example b"],
            ["cp a.example b", "cp -n a.example b\n`cp deploy/worker.remote.example.yaml x`"],
            id="example-copy",
        ),
        pytest.param(
            download_ok,
            _DOWNLOAD_GOOD,
            [
                "curl -o $OUT.part u\nmv -- $OUT.part $OUT",
                "curl -o '${OUT}.part' u\nmv ${OUT}.part ${OUT}",
                'curl --output "$OUT.part" u\nmv "$OUT.part" "$OUT"',
                'curl -fsS u > "$OUT.part"\nmv "$OUT.part" "$OUT"',
            ],
            [
                'curl -o "$OUT" u',
                "curl -o $OUT u\nmv $OUT.part $OUT",  # 去引号的直写
                'curl -o "$OUT.part" u',  # 不改名
                # --output / 重定向 / 组合短 flag 同样是直写（#1115）
                'curl --output "$OUT" u\n' + _DOWNLOAD_GOOD,
                'curl --output="$OUT" u\n' + _DOWNLOAD_GOOD,
                'curl -fsS u > "$OUT"\n' + _DOWNLOAD_GOOD,
                'curl -fsSLo "$OUT" u\n' + _DOWNLOAD_GOOD,
            ],
            id="download",
        ),
        pytest.param(
            delete_empty_ok,
            "volume.deleteEmpty -quietFor=1h\nvolume.deleteEmpty -quietFor=1h -apply",
            ["volume.deleteEmpty\nvolume.deleteEmpty -apply -quietFor=24h"],
            [
                "volume.deleteEmpty -quietFor=1h -apply",
                "volume.deleteEmpty -apply\nvolume.deleteEmpty",  # 预览在后
            ],
            id="delete-empty",
        ),
    ],
)
def test_checkers_reject_mutants_and_accept_equivalents(
    check: Callable[[str], bool], good: str, equivalents: list[str], mutants: list[str]
) -> None:
    assert check(good)
    for variant in equivalents:
        assert check(variant), f"等价改写被误判: {variant!r}"
    for variant in mutants:
        assert not check(variant), f"变异未被拦下: {variant!r}"


def test_write_targets_ignore_fd_duplication_and_read_redirects() -> None:
    block = 'echo x >&2\ncmd 2>&1 </etc/hosts\ndiff <(a) >(b)\ncmd 2>/dev/null\ncmd &> "$LOG"'
    assert [target for _, target in write_targets(block)] == ["/dev/null", "$LOG"]


# —— 对文档与脚本的实际断言 ————————————————————————————————


def test_ensure_s3_bucket_script_only_swallows_missing_cors() -> None:
    source = (ROOT / "scripts/ensure-s3-bucket.py").read_text(encoding="utf-8")
    assert cors_read_violations(source) == []


def test_doc_cors_merge_only_swallows_missing_cors() -> None:
    blocks = [code for _, code in _blocks(MATERIALS) if "put_bucket_cors" in code]
    assert blocks, "materials 文档里没有 CORS 合并片段——守卫失效"
    for block in blocks:
        assert cors_read_violations(_python_cors_body(block)) == []


def _blocks_with(doc: str, marker: str) -> list[str]:
    found = [code for _, code in _blocks(doc) if marker in code]
    assert found, f"{doc}: 没有含 {marker!r} 的代码块——守卫失效"
    return found


@pytest.mark.parametrize(
    ("doc", "marker", "check"),
    [
        (WORKER, "deploy/secrets/postgres_password", secret_generation_ok),
        (MATERIALS, "AGENT_LEGION_S3_SECRET_KEY=", s3_credentials_ok),
        (MATERIALS, "AGENT_LEGION_S3_PUBLIC_ENDPOINT", env_edit_ok),
        (WORKER, "LLM_GATEWAY_TOKEN", env_edit_ok),
        (API_TOKENS, "--compressed", download_ok),
        (MATERIALS, "volume.deleteEmpty", delete_empty_ok),
    ],
)
def test_destructive_example_writes_are_guarded(
    doc: str, marker: str, check: Callable[[str], bool]
) -> None:
    # 不按标记或命令名跳过任何块（#1115）：只读块由判定器自身按「无写入」放行。
    for block in _blocks_with(doc, marker):
        assert check(block), f"{doc}: 含 {marker!r} 的示例未通过守卫 {check.__name__}"


@pytest.mark.parametrize("doc", OPS_DOCS)
def test_env_writes_are_guarded(doc: str) -> None:
    for _, block in _blocks(doc):
        assert env_appends_guarded(block), f"{doc}: 未经存在性检查直写 deploy/.env 会重复追加键"


@pytest.mark.parametrize("doc", (WORKER,))
def test_example_copies_do_not_clobber(doc: str) -> None:
    assert example_copies_ok(_text(doc)), f"{doc}: 复制 *.example* 的命令须带 -n"


def test_s3_sync_migration_warns_about_target() -> None:
    assert sync_note_ok(_text(MATERIALS))
