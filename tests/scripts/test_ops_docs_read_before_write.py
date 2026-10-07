"""运维文档示例的「读成功再写」守卫（#1099 G2 review）。

codex 在 materials-storage-deployment.md 的 prod CORS 合并脚本上发现：
`get_bucket_cors` 的任意 ClientError 都被当成「还没有 CORS」，随后
`put_bucket_cors` 用只含新 origin 的规则**整份替换**现有配置——读失败
（权限、限流、凭据）就静默删掉生产 origin。同类模式在运维文档示例里
不止一处：重跑即覆盖已有密钥、生成失败把空值写进去、下载失败用残缺
字节覆盖已有文件。本测试按审计表逐条钉住修正后的形态，回退任一处即红。
纯静态解析 docs，不碰 DB。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
_FENCE = re.compile(r"^[ \t]*```(.*)$")


def _blocks(doc: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in (ROOT / doc).read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
        elif current is not None:
            current.append(line)
    assert current is None, f"{doc}: 代码块 fence 未配平"
    return blocks


def _blocks_with(doc: str, marker: str) -> list[str]:
    found = [block for block in _blocks(doc) if marker in block]
    assert found, f"{doc}: 没有含 {marker!r} 的代码块——守卫失效"
    return found


def test_cors_merge_only_treats_missing_configuration_as_empty() -> None:
    """put_bucket_cors 是整份替换：读失败只允许 NoSuchCORSConfiguration 一种
    当空规则，其它 ClientError 必须重新抛出（临时 SeaweedFS 实测：只有
    Write 权限的凭据读 403、写成功，旧脚本因此删掉了已有 origin）。"""
    for doc in ("docs/materials-storage-deployment.md",):
        for block in _blocks_with(doc, "put_bucket_cors"):
            assert "get_bucket_cors" in block
            handler = block[block.index("get_bucket_cors") : block.index("put_bucket_cors")]
            assert '!= "NoSuchCORSConfiguration"' in handler, doc
            assert "raise" in handler, doc
            # 不允许「except ClientError: rules = []」式的整类吞掉。
            assert not re.search(r"except [\w.]*ClientError:\s*\n\s*rules = \[\]", handler), doc


@pytest.mark.parametrize(
    ("doc", "marker", "guard"),
    [
        # 部署机密钥：存在即跳过（重跑不覆盖），先写临时文件、非空才改名。
        ("docs/agent-worker-deployment.md", "openssl rand -hex 32", '[ -e "$target" ]'),
        ("docs/agent-worker-deployment.md", "openssl rand -hex 32", '[ -s "$target.tmp" ]'),
        # S3 凭据：已有即不追加；随机值先落变量并判非空再写。
        (
            "docs/materials-storage-deployment.md",
            "AGENT_LEGION_S3_SECRET_KEY=",
            "grep -q '^AGENT_LEGION_S3_ACCESS_KEY='",
        ),
        (
            "docs/materials-storage-deployment.md",
            "AGENT_LEGION_S3_SECRET_KEY=",
            '[ -n "$SECRET_KEY" ]',
        ),
        # 示例配置复制：-n 不覆盖已填好的文件。
        ("docs/agent-worker-deployment.md", "velites-provider.env.example", "cp -n "),
        # 产物下载：先写 .part，成功才改名。
        ("docs/workspace-api-tokens.md", "curl -fsS --compressed", 'mv "$OUT.part" "$OUT"'),
    ],
)
def test_destructive_example_writes_are_guarded(doc: str, marker: str, guard: str) -> None:
    for block in _blocks_with(doc, marker):
        assert guard in block, f"{doc}: 含 {marker!r} 的示例缺少守卫 {guard!r}"


@pytest.mark.parametrize(
    ("doc", "forbidden"),
    [
        # 直接重定向到密钥文件：重跑即覆盖，生成命令失败也会留下空文件。
        (
            "docs/agent-worker-deployment.md",
            re.compile(r"^\s*openssl rand[^\n]*> deploy/secrets/", re.M),
        ),
        ("docs/agent-worker-deployment.md", re.compile(r"^\s*> deploy/secrets/", re.M)),
        # heredoc 里现场 $(openssl rand …)：生成失败照样把空值追加进 .env。
        (
            "docs/materials-storage-deployment.md",
            re.compile(r"=\$\(openssl rand[^\n]*\)\s*$", re.M),
        ),
        # 产物直接下载到最终文件名：失败时残缺字节覆盖已有文件。
        ("docs/workspace-api-tokens.md", re.compile(r'curl [^\n]*-o "\$OUT" ')),
    ],
)
def test_unguarded_destructive_forms_are_absent(doc: str, forbidden: re.Pattern[str]) -> None:
    for block in _blocks(doc):
        assert not forbidden.search(block), f"{doc}: 出现未守卫的破坏性写法 {forbidden.pattern}"
