// 清理、监控与执行租约的基础运行字段组。

import type { FieldGroup } from './instanceSettingsFieldTypes'

export const CORE_FIELD_GROUPS: FieldGroup[] = [
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
]
