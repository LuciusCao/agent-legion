// 实例设置表单的字段元数据表（从 InstanceSettingsSection 拆出以控制体积预算；
// 各组用户视角说明在 instanceSettingsHints.ts）。
// #389：code_capacity（本地兜底执行并发上限，实例级，重启生效）在
// 「本地执行」组，0 = 纯远程模式（不在宿主本地执行 code 节点）。
// 保留策略类字段（materials/execution retention）见姊妹文件
// instanceSettingsRetentionFields.ts。

import type { FieldGroup, NumberFieldDef } from './instanceSettingsFieldTypes'
import { RETENTION_FIELD_GROUPS } from './instanceSettingsRetentionFields'

export type { FieldGroup, NumberFieldDef } from './instanceSettingsFieldTypes'
export { RETENTION_FIELD_GROUPS } from './instanceSettingsRetentionFields'

export const FIELD_GROUPS: FieldGroup[] = [
  {
    title: '清理',
    fields: [
      {
        path: 'cleanup.log_retention_days',
        label: '日志保留天数',
        integer: true,
      },
      {
        path: 'cleanup.run_dir_retention_days',
        label: '运行目录保留天数',
        integer: true,
      },
      {
        path: 'cleanup.interval_seconds',
        label: '清理间隔（秒）',
        integer: true,
      },
    ],
    toggles: [],
  },
  {
    title: '监控',
    fields: [
      {
        path: 'monitoring.sample_interval_seconds',
        label: '采样间隔（秒）',
        integer: false,
      },
      {
        path: 'monitoring.retention_days',
        label: '数据保留天数',
        integer: true,
      },
    ],
    toggles: [],
  },
  {
    title: '心跳与租约',
    fields: [
      {
        path: 'heartbeat_interval_seconds',
        label: '心跳间隔（秒）',
        integer: false,
      },
      { path: 'lease_ttl_seconds', label: '租约 TTL（秒）', integer: true },
      {
        path: 'heartbeat_failure_threshold',
        label: '心跳失败阈值',
        integer: true,
      },
    ],
    toggles: [],
  },
  {
    title: 'Sweeper',
    fields: [
      {
        path: 'sweeper_interval_seconds',
        label: '扫描间隔（秒）',
        integer: false,
      },
    ],
    toggles: [{ path: 'sweeper_enabled', label: '启用 sweeper' }],
  },
  {
    title: '运行与本地执行',
    fields: [
      // #358：单次 run 条目上限（0 不限制），超限提交被 API 拒绝。
      // workflows.enabled 已随 #385/#389 退役（部署形态由 code_capacity 表达）。
      {
        path: 'workflows.max_items_per_run',
        label: '单次 run 条目上限（0 不限制）',
        integer: true,
        allowZero: true,
      },
      {
        path: 'code_capacity',
        label: '本地执行并发上限（0 = 纯远程模式）',
        integer: true,
        allowZero: true,
      },
    ],
    toggles: [],
  },
  ...RETENTION_FIELD_GROUPS,
  {
    title: 'Worker 限制',
    fields: [
      {
        path: 'agent_workers.max_archive_bytes',
        label: '归档大小上限（字节）',
        integer: true,
      },
      {
        path: 'agent_workers.min_protocol_version',
        label: '最低协议版本',
        integer: true,
      },
    ],
    toggles: [],
  },
]

export function fieldDef(path: string): NumberFieldDef {
  const field = FIELD_GROUPS.flatMap((g) => g.fields).find(
    (f) => f.path === path
  )
  if (!field) throw new Error(`unknown instance settings field: ${path}`)
  return field
}
