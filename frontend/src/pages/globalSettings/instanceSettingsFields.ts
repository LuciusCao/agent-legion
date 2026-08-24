// 实例设置表单的字段元数据表（从 InstanceSettingsSection 拆出以控制体积预算）。
// P-0.5：code_capacity（内置 code 池容量，实例级，重启生效）在「代码池」组。

export interface NumberFieldDef {
  path: string
  label: string
  integer: boolean
  // 允许 0（语义为「关闭」的字段，如材料 TTL）；缺省要求 > 0。
  allowZero?: boolean
}

export interface ToggleDef {
  path: string
  label: string
}

export interface FieldGroup {
  title: string
  fields: NumberFieldDef[]
  toggles: ToggleDef[]
}

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
    title: '功能开关',
    fields: [],
    toggles: [{ path: 'workflows.enabled', label: '启用工作流' }],
  },
  {
    title: '代码池',
    fields: [{ path: 'code_capacity', label: 'code 池容量', integer: true }],
    toggles: [],
  },
  {
    title: '材料',
    fields: [
      {
        path: 'materials_ttl_days',
        label: '材料保留天数（0 关闭）',
        integer: true,
        allowZero: true,
      },
    ],
    toggles: [],
  },
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
  for (const group of FIELD_GROUPS) {
    for (const field of group.fields) {
      if (field.path === path) return field
    }
  }
  throw new Error(`unknown instance settings field: ${path}`)
}
