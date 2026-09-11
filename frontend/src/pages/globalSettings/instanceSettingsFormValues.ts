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
    'workflows.max_items_per_run': String(doc.workflows.max_items_per_run),
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
    'agent_enqueue.workers': String(doc.agent_enqueue.workers),
    'agent_enqueue.max_pending': String(doc.agent_enqueue.max_pending),
    'result_unpack.workers': String(doc.result_unpack.workers),
    'result_validate.workers': String(doc.result_validate.workers),
    'agent_claim.worker_touch_interval_seconds': String(
      doc.agent_claim.worker_touch_interval_seconds
    ),
  }
}
