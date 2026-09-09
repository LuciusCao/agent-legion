// 实例设置表单的值转换/校验与 PUT 载荷构建（从 instanceSettingsForm 拆出
// 以控制体积预算；#509/#554 的容量旋钮字段同表登记）。

import type {
  InstanceSettingsResponse,
  InstanceSettingsUpdate,
} from '../../api/instanceSettings'
import { fieldDef } from './instanceSettingsFields'

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
    'agent_enqueue.workers': String(doc.agent_enqueue.workers),
    'agent_enqueue.max_pending': String(doc.agent_enqueue.max_pending),
    'result_unpack.workers': String(doc.result_unpack.workers),
  }
}
function parseNumber(values: FormValues, path: string): number {
  const def = fieldDef(path)
  const raw = String(values[path] ?? '').trim()
  const parsed = Number(raw)
  if (
    !raw ||
    !Number.isFinite(parsed) ||
    parsed < 0 ||
    (!def.allowZero && parsed === 0)
  ) {
    throw new Error(
      `${def.label} 必须是${def.allowZero ? '非负' : '大于 0 的'}数字`
    )
  }
  const value = def.integer ? Math.round(parsed) : parsed
  if (def.integer && value < (def.allowZero ? 0 : 1)) {
    throw new Error(
      `${def.label} 必须是${def.allowZero ? '非负整数' : '不小于 1 的整数'}`
    )
  }
  return value
}

export function buildPayload(values: FormValues): InstanceSettingsUpdate {
  return {
    cleanup: {
      log_retention_days: parseNumber(values, 'cleanup.log_retention_days'),
      run_dir_retention_days: parseNumber(
        values,
        'cleanup.run_dir_retention_days'
      ),
      interval_seconds: parseNumber(values, 'cleanup.interval_seconds'),
    },
    monitoring: {
      sample_interval_seconds: parseNumber(
        values,
        'monitoring.sample_interval_seconds'
      ),
      retention_days: parseNumber(values, 'monitoring.retention_days'),
    },
    heartbeat_interval_seconds: parseNumber(
      values,
      'heartbeat_interval_seconds'
    ),
    lease_ttl_seconds: parseNumber(values, 'lease_ttl_seconds'),
    heartbeat_failure_threshold: parseNumber(
      values,
      'heartbeat_failure_threshold'
    ),
    sweeper_enabled: Boolean(values.sweeper_enabled),
    sweeper_interval_seconds: parseNumber(values, 'sweeper_interval_seconds'),
    code_capacity: parseNumber(values, 'code_capacity'),
    materials_ttl_days: parseNumber(values, 'materials_ttl_days'),
    execution_retention_days: parseNumber(values, 'execution_retention_days'),
    workflows: {
      max_items_per_run: parseNumber(values, 'workflows.max_items_per_run'),
    },
    agent_workers: {
      max_archive_bytes: parseNumber(values, 'agent_workers.max_archive_bytes'),
      min_protocol_version: parseNumber(
        values,
        'agent_workers.min_protocol_version'
      ),
      max_concurrent_result_commits: parseNumber(
        values,
        'agent_workers.max_concurrent_result_commits'
      ),
    },
    agent_enqueue: {
      workers: parseNumber(values, 'agent_enqueue.workers'),
      max_pending: parseNumber(values, 'agent_enqueue.max_pending'),
    },
    result_unpack: {
      workers: parseNumber(values, 'result_unpack.workers'),
    },
  }
}
