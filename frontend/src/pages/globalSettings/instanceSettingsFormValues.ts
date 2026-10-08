// 实例设置表单初值（doc → 表单值）与表单值类型；从 instanceSettingsPayload.ts
// 拆出以控制体积预算（#612 再基线后 payload 贴墙，双向转换各占半边：
// 本文件管读方向，payload 管校验与 PUT 载荷构建）。

import type { InstanceSettingsResponse } from '../../api/instanceSettings'

export type FormValues = Record<string, string | boolean>

export function toFormValues(doc: InstanceSettingsResponse): FormValues {
  return {
    'cleanup.log_retention_days': String(doc.cleanup.log_retention_days),
    'cleanup.run_dir_retention_days': String(
      doc.cleanup.run_dir_retention_days
    ),
    'cleanup.interval_seconds': String(doc.cleanup.interval_seconds),
    'monitoring.sample_interval_seconds': String(
      doc.monitoring.sample_interval_seconds
    ),
    'monitoring.retention_days': String(doc.monitoring.retention_days),
    heartbeat_interval_seconds: String(doc.heartbeat_interval_seconds),
    lease_ttl_seconds: String(doc.lease_ttl_seconds),
    heartbeat_failure_threshold: String(doc.heartbeat_failure_threshold),
    sweeper_enabled: doc.sweeper_enabled,
    sweeper_interval_seconds: String(doc.sweeper_interval_seconds),
    code_capacity: String(doc.code_capacity),
    materials_ttl_days: String(doc.materials_ttl_days),
    execution_retention_days: String(doc.execution_retention_days),
    studio_chat_retention_days: String(doc.studio_chat_retention_days),
    studio_chat_terminal_grant_required: doc.studio_chat_terminal_grant_required,
    'workflows.max_items_per_run': String(doc.workflows.max_items_per_run),
    // 契约是字节，表单按 KB 展示（验收反馈 #786）：Math.round 对齐
    // WorkflowNodeCodeEditor 的 KB 展示先例；非 1024 倍数只能经 env/直调
    // API 产生，回显取整、再次保存归一为整数 KB。
    'workflows.node_code_max_bytes': String(
      Math.round(doc.workflows.node_code_max_bytes / 1024)
    ),
    'agent_workers.max_archive_bytes': String(
      doc.agent_workers.max_archive_bytes
    ),
    'agent_workers.min_protocol_version': String(
      doc.agent_workers.min_protocol_version
    ),
    'agent_workers.max_concurrent_result_commits': String(
      doc.agent_workers.max_concurrent_result_commits
    ),
    'agent_workers.result_commit_batching':
      doc.agent_workers.result_commit_batching,
    'agent_workers.artifact_spot_check_percent': String(
      doc.agent_workers.artifact_spot_check_percent
    ),
    'agent_workers.artifact_download_presign_ttl_seconds': String(
      doc.agent_workers.artifact_download_presign_ttl_seconds
    ),
    'agent_enqueue.workers': String(doc.agent_enqueue.workers),
    'agent_enqueue.max_pending': String(doc.agent_enqueue.max_pending),
    'result_unpack.workers': String(doc.result_unpack.workers),
    'result_validate.workers': String(doc.result_validate.workers),
    'agent_claim.worker_touch_interval_seconds': String(
      doc.agent_claim.worker_touch_interval_seconds
    ),
    csp_script_unsafe_inline: doc.csp_script_unsafe_inline,
  }
}
