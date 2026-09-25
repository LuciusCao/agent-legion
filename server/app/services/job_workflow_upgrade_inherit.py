"""Inherit-mode reachability guard for job workflow upgrade (issue #645).

继承模式在重置前校验「拟继承节点的产物确实可达」：对象存储权威层启用
时（``require_manifest_rows=True``）每个声明 output 都必须有
``job_artifacts`` 清单行——本地 job_dir 只是可淘汰缓存
（EXEC-ARTIFACT-STORE-001），执行前 ``restore_missing_inputs`` 只能按
清单行回填，仅有本地文件（上传失败/补传未完成）的节点判为可继承会在
缓存淘汰后让下游永久等缺失输入（codex #776 复审 P2）。权威层未配置
（裸构造服务/无对象存储）时本地文件是唯一副本，回落为「本地文件或
清单行」的旧判定。两者皆无（产物被淘汰且清单缺失、或从未产出）时该
节点退化为重跑——宁可多跑，不可让下游等一个永远不出现的输入。

校验是纯读路径：清单行查询走 ``JobQueries`` 门面
（``job_artifact_manifest_names_for_nodes``，BOUNDARY-DATA-001），本地
文件存在性由调用方用 jobs_dir 判断（文件系统不是数据库）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.storage_paths import ManagedPathError, resolve_job_dir


def _declared_outputs(job: dict[str, Any], node_keys: frozenset[str]) -> dict[str, list[str]]:
    """node_key → 该节点在新定义里声明的 outputs（快照已指向新 revision）。

    继承候选节点按定义未变（变了就在变更集里），新旧 outputs 相同，用
    新快照解析即可。
    """
    definition = definition_from_job_snapshot(job)
    if definition is None:
        return {}
    return {
        key: list(node.outputs)
        for key, node in definition.executable_nodes.items()
        if key in node_keys
    }


def unreachable_inherit_nodes(
    job_db: JobQueries,
    job: dict[str, Any],
    jobs_dir: Path,
    candidate_nodes: frozenset[str],
    *,
    require_manifest_rows: bool = False,
) -> frozenset[str]:
    """拟继承节点中产物不可达的子集（调用方把这些节点并回重跑集）。

    判定（按节点逐 output）：无声明 outputs 的节点无依赖面，恒可达。
    ``require_manifest_rows=True``（对象存储权威层启用）：任一 output
    无 ``job_artifacts`` 清单行即不可达——本地文件是可淘汰缓存，不能
    充当可达性证据（EXEC-ARTIFACT-STORE-001，codex #776 复审 P2）。
    否则（权威层未配置）：任一 output 既不在本地 job_dir、也无清单行
    → 该节点不可达（部分产物缺即缺，下游 restore 不出来）。快照解析
    失败（definition_from_job_snapshot → None）时保守全部退化。
    """
    if not candidate_nodes:
        return frozenset()
    outputs_by_node = _declared_outputs(job, candidate_nodes)
    if not outputs_by_node:
        # 快照解析失败：无法证明可达性，保守全量退化（clean 语义）。
        return candidate_nodes
    job_dir: Path | None = None
    if not require_manifest_rows:
        try:
            job_dir = resolve_job_dir(job, jobs_dir)
        except ManagedPathError:
            # 存储路径逃逸管理根：继承前可达性预检的失败语义只能是「不可达
            # → 退化重跑」，不能让升级整体失败。退化是安全方向（多跑不少跑）。
            return candidate_nodes
    manifest_names = job_db.job_artifact_manifest_names_for_nodes(str(job["id"]), candidate_nodes)
    unreachable: set[str] = set()
    for node_key, outputs in outputs_by_node.items():
        if not outputs:
            continue
        for name in outputs:
            if (node_key, name) in manifest_names:
                continue
            if job_dir is not None and (job_dir / name).exists():
                continue
            unreachable.add(node_key)
            break
    return frozenset(unreachable)
