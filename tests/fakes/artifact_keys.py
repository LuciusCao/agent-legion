"""#853 不可变版本 key 布局下的测试辅助。

#853 起 authority key 是一次性版本 key（``jobs/{ws}/{job}/.v/{version}/{name}``），
测试不再能按名字拼出固定 key——按清单行的 ``storage_key`` 取「清单指向的
字节」，这正是读路径（raw / 直连 URL）实际服务的对象。

``pin_legacy_authority_keys``：只给验证共享 promote primitive **覆盖契约**的
用例用（回滚备份 / 恢复臂 / 按 key 串行化）。primitive 本身与 key 布局无关、
仍支持覆盖既有 authority 对象；#853 后生产写入不再覆盖，这些臂只能经把
版本 key 钉回 #853 前固定布局的方式覆盖到（等价于「存量固定 key 被覆盖」
的场景），否则会变成永不执行的死覆盖。
"""

from __future__ import annotations

from typing import Any

import pytest


def manifest_key(store: Any, job_id: str, name: str) -> str:
    """清单行（同名取最新行，与 raw 读同一决胜）指向的 storage_key。"""
    row = store.lookup(job_id, name)
    assert row is not None, f"no manifest row for {job_id}/{name}"
    return str(row["storage_key"])


def manifest_bytes(store: Any, job_id: str, name: str) -> bytes:
    """清单行指向的对象字节（FakeObjectStorage）。"""
    return bytes(store.storage.objects[manifest_key(store, job_id, name)])


def pin_legacy_authority_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """把两处写入口的版本 key 派生钉回 #853 前的固定 ``jobs/{ws}/{job}/{name}``。"""
    import server.app.agent_broker.remote_artifact_promote as remote_promote
    import server.app.services.job_artifact_versions as versions
    from server.app.services.job_artifact_objects import artifact_storage_key

    def _fixed(workspace_id: str, job_id: str, version: str, name: str) -> str:
        del version
        return artifact_storage_key(workspace_id, job_id, name)

    # 本地上传臂在调用时 lazy import（读模块属性），远端 promote 臂模块级导入。
    monkeypatch.setattr(versions, "artifact_version_key", _fixed)
    monkeypatch.setattr(remote_promote, "artifact_version_key", _fixed)
