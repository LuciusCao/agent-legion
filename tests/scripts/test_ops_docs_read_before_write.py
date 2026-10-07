"""运维文档示例与 ensure-s3-bucket.py 的「读成功再写」守卫（#1099 G2 review、#1111）。

codex 在 materials-storage-deployment.md 的 prod CORS 合并脚本上发现：
`get_bucket_cors` 的任意 ClientError 都被当成「还没有 CORS」，随后
`put_bucket_cors` 用只含新 origin 的规则**整份替换**现有配置——读失败
（权限、限流、凭据）就静默删掉生产 origin。同类模式在运维文档示例里不止
一处：重跑即覆盖已有密钥、生成失败把空值写进去、读不到 .env 却整份重写、
下载失败用残缺字节覆盖已有文件、未预览就执行删除。

判定是语义级的：Python 片段用 ast 解析 except 子句（注释与字符串里的
「raise」不算数），shell 片段用容忍引号 / test 写法 / `mv --` 等等价改写的
宽松正则。每个判定器另有一组「变异必须红、等价改写必须绿」的自检用例，
防止判定器本身退化成字面量比对。纯静态，不碰 DB。
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


# —— 判定器 ———————————————————————————————————————————————


def _is_client_error(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "ClientError"
    if isinstance(node, ast.Attribute):
        return node.attr == "ClientError"
    return False


def _compares_missing_cors(test: ast.expr) -> tuple[bool, bool]:
    """(是否比对 NoSuchCORSConfiguration, 比较符是否为 !=)。"""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False, False
    operands = [test.left, *test.comparators]
    hit = any(
        isinstance(item, ast.Constant) and item.value == "NoSuchCORSConfiguration"
        for item in operands
    )
    return hit, isinstance(test.ops[0], ast.NotEq)


def _raises(statements: list[ast.stmt]) -> bool:
    return any(isinstance(stmt, ast.Raise) for stmt in statements)


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
            guarded = False
            for stmt in handler.body:
                if not isinstance(stmt, ast.If):
                    continue
                hit, not_equal = _compares_missing_cors(stmt.test)
                if not hit:
                    continue
                # != 形态：if 体必须 raise；== 形态：else 必须 raise。
                guarded = _raises(stmt.body) if not_equal else _raises(stmt.orelse)
            if not guarded:
                problems.append("except ClientError 未在非 NoSuchCORSConfiguration 时 raise")
    if not found:
        problems.append("未找到包住 get_bucket_cors 的 try")
    return problems


_Q = r"[\"']?"  # 可选引号
_TEST_E = re.compile(r"(?:\[\s+|test\s+)-e\s+" + _Q + r"\$\{?\w+")
_TEST_S = re.compile(r"(?:\[\s+|test\s+)-s\s+" + _Q + r"\$\{?\w+\}?\.tmp")
_MV_TMP = re.compile(r"\bmv\s+(?:--\s+)?" + _Q + r"\$\{?\w+\}?\.tmp" + _Q + r"\s")
_SECRET_DIRECT = re.compile(r"(?:>>?|\btee\b(?:\s+-a)?)\s*" + _Q + r"(?:\./)?deploy/secrets/")


def secret_generation_ok(block: str) -> bool:
    return (
        not _SECRET_DIRECT.search(block)
        and bool(_TEST_E.search(block))
        and bool(_TEST_S.search(block))
        and bool(_MV_TMP.search(block))
    )


_S3_KEYS = ("BUCKET", "ENDPOINT", "ACCESS_KEY", "SECRET_KEY")
_INLINE_RANDOM = re.compile(r"^\s*AGENT_LEGION_S3_\w+=\$\(", re.M)
_TEST_N = re.compile(r"(?:\[\s+|test\s+)-n\s+" + _Q + r"\$\{?SECRET_KEY")


def s3_credentials_ok(block: str) -> bool:
    guard = next((line for line in block.splitlines() if re.search(r"\bgrep\s+-\w*q", line)), "")
    return (
        all(key in guard for key in _S3_KEYS)
        and not _INLINE_RANDOM.search(block)
        and bool(_TEST_N.search(block))
        and bool(re.search(r"^\s*else\b[\s\S]*>&2", block, re.M))
    )


_ENV_APPEND = re.compile(r"\becho\b[^\n]*>>\s*" + _Q + r"(?:\./)?deploy/\.env")
_GREP_RC = re.compile(r"\[\s+\$\?\s+-le\s+1\s+\]")


def env_edit_ok(block: str) -> bool:
    """单键写 .env：不得 echo >> 追加；替换式改写须区分 grep 的「无匹配」(1)
    与「读失败」(2)，读失败中止。"""
    if _ENV_APPEND.search(block):
        return False
    if "grep -v" in block:
        return bool(_GREP_RC.search(block)) and bool(_MV_TMP.search(block))
    return True


_CP_EXAMPLE = re.compile(r"\bcp\b((?:\s+-\S+)*)\s+\S*example\S*")


def example_copies_ok(text: str) -> bool:
    copies = _CP_EXAMPLE.findall(text)
    return bool(copies) and all(
        re.search(r"(?:^|\s)(?:-\w*n\w*|--no-clobber)\b", f) for f in copies
    )


_DOWNLOAD_FINAL = re.compile(r"-o\s+" + _Q + r"\$\{?OUT\}?" + _Q + r"(?=\s|$)")
_MV_PART = re.compile(
    r"\bmv\s+(?:--\s+)?" + _Q + r"\$\{?OUT\}?\.part" + _Q + r"\s+" + _Q + r"\$\{?OUT\}?" + _Q
)


def download_ok(block: str) -> bool:
    return not _DOWNLOAD_FINAL.search(block) and bool(_MV_PART.search(block))


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
_CORS_EQUIVALENT = """
try:
    rules = c.get_bucket_cors(Bucket=b)["CORSRules"]
except botocore.exceptions.ClientError as err:
    code = err.response["Error"]["Code"]
    if "NoSuchCORSConfiguration" == code:
        rules = []
    else:
        raise
"""
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
}


@pytest.mark.parametrize(
    ("check", "good", "equivalents", "mutants"),
    [
        pytest.param(
            lambda s: not cors_read_violations(s),
            _CORS_OK,
            [_CORS_EQUIVALENT],
            list(_CORS_MUTANTS.values()),
            id="cors",
        ),
        pytest.param(
            secret_generation_ok,
            'if [ -e "$target" ]; then :; fi\n"$@" > "$target.tmp" && [ -s "$target.tmp" ]'
            ' && mv "$target.tmp" "$target"',
            [
                "if test -e $t; then :; fi\ncmd > $t.tmp && test -s $t.tmp && mv -- $t.tmp $t",
                "if [ -e '${t}' ]; then :; fi\ncmd > ${t}.tmp && [ -s ${t}.tmp ] && mv ${t}.tmp ${t}",
            ],
            [
                "openssl rand -hex 32 > deploy/secrets/postgres_password",
                "openssl rand -hex 32 | tee deploy/secrets/postgres_password",
                "openssl rand -hex 32 | tee -a ./deploy/secrets/x",
                'cmd > "$target.tmp" && mv "$target.tmp" "$target"',  # 缺存在检查与非空检查
                'if [ -e "$t" ]; then :; fi\ncmd > "$t.tmp" && mv "$t.tmp" "$t"',  # 缺非空检查
            ],
            id="secrets",
        ),
        pytest.param(
            s3_credentials_ok,
            "if grep -qE '^AGENT_LEGION_S3_(BUCKET|ENDPOINT|ACCESS_KEY|SECRET_KEY)=' f; then\n"
            ':\nelif A=$(x) && SECRET_KEY=$(y) && [ -n "$SECRET_KEY" ]; then\n'
            "cat >> f <<EOF\nAGENT_LEGION_S3_SECRET_KEY=$SECRET_KEY\nEOF\nelse\n  echo bad >&2\nfi",
            [
                "if grep -Eq '^AGENT_LEGION_S3_(BUCKET|ENDPOINT|ACCESS_KEY|SECRET_KEY)=' f; then\n"
                ":\nelif SECRET_KEY=$(y) && test -n $SECRET_KEY; then\n:\nelse\n  echo bad >&2\nfi",
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
            ],
            id="s3-credentials",
        ),
        pytest.param(
            env_edit_ok,
            'grep -v "^$2=" "$1" > "$1.tmp"\n[ $? -le 1 ] || return 1\nmv "$1.tmp" "$1"',
            ["grep -v ^$2= $1 > $1.tmp\n[ $? -le 1 ] || exit 1\nmv -- $1.tmp $1"],
            [
                "echo 'K=v' >> deploy/.env",
                'echo "K=v" >> "./deploy/.env"',
                'grep -v "^$2=" "$1" > "$1.tmp"\nmv "$1.tmp" "$1"',  # 读失败不中止
            ],
            id="env-edit",
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
            'curl -o "$OUT.part" u\nmv "$OUT.part" "$OUT"',
            [
                "curl -o $OUT.part u\nmv -- $OUT.part $OUT",
                "curl -o '${OUT}.part' u\nmv ${OUT}.part ${OUT}",
            ],
            [
                'curl -o "$OUT" u',
                "curl -o $OUT u\nmv $OUT.part $OUT",  # 去引号的直写
                'curl -o "$OUT.part" u',  # 不改名
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
    for block in _blocks_with(doc, marker):
        if marker == "deploy/secrets/postgres_password" and "openssl" not in block:
            continue  # chmod 等只读 / 权限命令所在的块
        if marker == "AGENT_LEGION_S3_PUBLIC_ENDPOINT" and "set_env" not in block:
            continue
        assert check(block), f"{doc}: 含 {marker!r} 的示例未通过守卫 {check.__name__}"


@pytest.mark.parametrize("doc", OPS_DOCS)
def test_no_single_key_env_append(doc: str) -> None:
    for _, block in _blocks(doc):
        assert not _ENV_APPEND.search(block), f"{doc}: echo >> deploy/.env 会重复追加键"


@pytest.mark.parametrize("doc", (WORKER,))
def test_example_copies_do_not_clobber(doc: str) -> None:
    assert example_copies_ok(_text(doc)), f"{doc}: 复制 *.example* 的命令须带 -n"


def test_s3_sync_migration_warns_about_target() -> None:
    assert sync_note_ok(_text(MATERIALS))
