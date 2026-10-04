"""Dispatch 冻结 input 身份（EXEC-INPUT-IDENTITY-001 的唯一解析点）。

INV-9（#876 P2-a）：序语义只由数组承载，dict 键序在任何序列化边界后
视为未定义——enqueue 的 manifest 持久化用 ``json.dumps(sort_keys=True)``
，声明列表序落库即换成键序。冻结点按归一化名去重：同一归一化名只读
一次源文件、只 put 一次 CAS、``input_artifacts`` 只记一个键（键用归
一化名）——双读竞态消失，Worker/Host/任何序无从分叉。声明列表
``manifest["inputs"]`` 原样保留（展示/审计语义），只有 refs dict 去
重。不安全名（绝对路径/``..``）与视图侧同一 ``safe_relative`` 语义跳
过——顺带关掉 dispatch 侧的越界读（此前 ``job_dir / "../x"`` 会被读
出；Worker 下载与校验视图本来就拒绝这类名）。
"""

from __future__ import annotations

from typing import Any

from server.app.executors.models import ExecutionContext
from server.app.services.artifact_store import ArtifactStore
from server.app.workflows.validation_view import safe_relative


def stage_agent_inputs(
    store: ArtifactStore, context: ExecutionContext, manifest: dict[str, Any]
) -> None:
    manifest["bundle_mode"] = "refs"
    manifest["artifact_upload_url"] = "/api/artifacts"
    refs: dict[str, str] = {}
    for relative_path in context.inputs:
        normalized = safe_relative(str(relative_path))
        if normalized is None or normalized in refs:
            continue
        digest = store.put((context.job_dir / normalized).read_bytes())
        store.add_ref(context.job_id, context.node_key, normalized, digest)
        refs[normalized] = f"sha256:{digest}"
    manifest["input_artifacts"] = refs
