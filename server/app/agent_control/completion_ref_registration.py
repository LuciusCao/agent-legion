"""上报产物的 artifact ref 登记与撞名守卫（#876 P2-1）。

自 ``completion_staged`` 拆出（文件体积预算）。两条不变量在此交汇：

- 登记必须在校验前完成——legacy 通道（字符串 ref）上传的 blob 靠这里
  登记 ref 才不被 GC：零引用窗口里校验排队 >600s grace + GC tick 会
  回收 blob 与 artifacts 行，事后 add_ref 撞 FK 缺失即 500 不可恢复；
- 共享 (job,node,name) 槽位由 input 冻结 ref 与 output 登记共用——
  归一化后「不在声明 outputs 且与声明 inputs 撞名」的上报条目跳过登
  记（撞名时冻结 input 优先，不 upsert 槽位；未声明产物反正不会被
  promote，其 blob 零引用被 GC 无害）。

RMW 名（同名 declared input+output）照常登记：视图对该名取产物字节是
既定语义。remote 通道（dict ref）名不登记（gated promote 已登记）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.workflows.validation_view import safe_relative

if TYPE_CHECKING:
    from server.app.agent_control.completion import AgentOutcome
    from server.app.services.artifact_store import ArtifactStore


def register_reported_output_refs(
    store: ArtifactStore,
    job_id: str,
    node_key: str,
    outcome: AgentOutcome,
    remote_names: Any,
    manifest: dict[str, Any],
    expected: tuple[str, ...],
) -> None:
    """登记 Worker 上报产物的 CAS ref（守卫语义见模块 docstring）。"""
    declared_inputs = {
        normalized
        for raw in manifest.get("inputs") or ()
        if (normalized := safe_relative(str(raw))) is not None
    }
    declared_outputs = {
        normalized for name in expected if (normalized := safe_relative(name)) is not None
    }
    for name, ref in outcome.output_artifacts.items():
        normalized = safe_relative(str(name))
        if name in remote_names or (
            normalized in declared_inputs and normalized not in declared_outputs
        ):
            continue
        store.add_ref(job_id, node_key, str(name), str(ref).split(":", 1)[-1])
