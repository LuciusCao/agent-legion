"""材料对象写入入口清单钉子（#1314）。

S3 孤儿 GC 的材料删除批依赖「所有材料对象写入都先让 materials 行引用其
key、后 PUT」。这里按 AST 枚举 server/ 与 scripts/ 下全部对象写入调用点，钉住清单：
新增写入点必须先经评审归入下表（并补 tests/services/
test_material_gc_write_protocol.py 的交错用例），而不是绕过协议。

- ``put_object``：只允许服务端材料写入 chokepoint
  （``material_object_publish``：intent 行 → PUT → 发布）；
- ``presign_put``：``materials.py``（presign 首发/重试与 repair 重签——行锁
  内派生 key 并提交后才签 URL）与 ``remote_artifact_support.py``
  （``jobs-staging/`` 前缀的产物直传，不在材料前缀）。

产物侧 ``put_stream`` / ``copy_object`` 由 scripts/architecture/
execution_write_surfaces.py 另行治理（jobs/ 前缀）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]

_ALLOWED = {
    "put_object": {"server/app/services/material_object_publish.py"},
    "presign_put": {
        "server/app/services/materials.py",
        "server/app/agent_broker/remote_artifact_support.py",
    },
}
# 存储适配层自身（协议定义与 boto3 转发），不是写入入口。
_STORAGE_LAYER = "server/app/storage/"


def _call_sites() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {name: set() for name in _ALLOWED}
    paths = [*(ROOT / "server").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]
    for path in sorted(paths):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(_STORAGE_LAYER):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in found
            ):
                found[node.func.attr].add(rel)
    return found


def test_material_object_write_surfaces_are_pinned() -> None:
    assert _call_sites() == _ALLOWED
