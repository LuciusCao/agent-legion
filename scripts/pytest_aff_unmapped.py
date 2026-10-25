"""Unmapped-path policy for affected-test selection."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_UNCOVERED_BACKEND_PREFIXES = ("config/", "deploy/", "examples/")


def _is_test_module(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") and name.endswith(".py")


def unmapped_source_files(
    changed: list[str], mapping: dict[str, list[str]], repo_root: Path | None = None
) -> list[str]:
    """Changed paths whose unknown impact requires the full unit fallback."""
    root = repo_root if repo_root is not None else REPO_ROOT
    unmapped: list[str] = []
    for path in changed:
        if path.startswith(_UNCOVERED_BACKEND_PREFIXES):
            unmapped.append(path)
            continue
        if path.startswith("tests/"):
            # codex 复审 P2（PR #792）：tests/ 下的 Python 辅助文件
            # （helpers/conftest 等）改动会影响其全部消费者，而 --cov 不覆盖
            # tests/ 树、索引无法映射受影响面——静默丢弃会让「helper + 可映射
            # 源码」的混合改动只跑局部子集（aff 内环误报通过）。计入 unmapped
            # 触发 exit 4 全量回退。test_*.py 由选择器直通/映射处理（其删除
            # 语义 = 用例随文件消失，nodeid 级丢弃即可，无需回退）；YAML/
            # JSON 等确定不影响测试执行的文件才可静默忽略。
            # 跟进 P2：删除形态（is_file() 为 False）也必须计入——已删除
            # helper 的消费者 import 即炸，而子集可能根本收集不到它们。
            if not _is_test_module(path) and path.endswith(".py"):
                unmapped.append(path)
            continue
        if mapping.get(path) is None and (root / path).is_file():
            unmapped.append(path)
    return sorted(unmapped)
