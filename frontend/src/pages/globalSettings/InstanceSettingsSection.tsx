import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FormControlLabel, Switch } from '@mui/material'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useUiStore } from '../../stores/uiStore'
import {
  getInstanceSettings,
  updateInstanceSettings,
} from '../../api/instanceSettings'
import type {
  InstanceSettingsResponse,
  InstanceSettingsUpdate,
} from '../../api/instanceSettings'
import styles from '../GlobalSettingsPage.module.css'

interface NumberFieldDef {
  path: string
  label: string
  integer: boolean
}

interface ToggleDef {
  path: string
  label: string
}

interface FieldGroup {
  title: string
  fields: NumberFieldDef[]
  toggles: ToggleDef[]
}

const FIELD_GROUPS: FieldGroup[] = [
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

type FormValues = Record<string, string | boolean>

function fieldDef(path: string): NumberFieldDef {
  for (const group of FIELD_GROUPS) {
    for (const field of group.fields) {
      if (field.path === path) return field
    }
  }
  throw new Error(`unknown instance settings field: ${path}`)
}

function toFormValues(doc: InstanceSettingsResponse): FormValues {
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
    'workflows.enabled': doc.workflows.enabled,
    'agent_workers.max_archive_bytes': String(
      doc.agent_workers.max_archive_bytes
    ),
    'agent_workers.min_protocol_version': String(
      doc.agent_workers.min_protocol_version
    ),
  }
}

function parseNumber(values: FormValues, path: string): number {
  const def = fieldDef(path)
  const raw = String(values[path] ?? '').trim()
  const parsed = Number(raw)
  if (!raw || !Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${def.label} 必须是大于 0 的数字`)
  }
  const value = def.integer ? Math.round(parsed) : parsed
  if (def.integer && value < 1) {
    throw new Error(`${def.label} 必须是不小于 1 的整数`)
  }
  return value
}

function buildPayload(values: FormValues): InstanceSettingsUpdate {
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
    workflows: { enabled: Boolean(values['workflows.enabled']) },
    agent_workers: {
      max_archive_bytes: parseNumber(values, 'agent_workers.max_archive_bytes'),
      min_protocol_version: parseNumber(
        values,
        'agent_workers.min_protocol_version'
      ),
    },
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function InstanceSettingsEditor({
  initial,
}: {
  initial: InstanceSettingsResponse
}) {
  const [values, setValues] = useState<FormValues>(() => toFormValues(initial))
  const [baseline, setBaseline] = useState(() =>
    JSON.stringify(toFormValues(initial))
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const isDirty = JSON.stringify(values) !== baseline

  async function handleSave() {
    setError('')
    setSaving(true)
    try {
      const result = await updateInstanceSettings(buildPayload(values))
      const next = toFormValues(result)
      setValues(next)
      setBaseline(JSON.stringify(next))
      useUiStore
        .getState()
        .showToast('实例设置已保存，重启服务后生效', 'success')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={styles.card}>
      <h3 className={styles.heading}>实例设置</h3>
      <p className={styles.hint}>全局运行时配置；保存后需重启服务才能生效。</p>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {FIELD_GROUPS.map((group) => (
        <div key={group.title}>
          <p className={styles.groupTitle}>{group.title}</p>
          {group.fields.map((field) => (
            <div className={styles.row} key={field.path}>
              <label
                className={styles.label}
                htmlFor={`instance-${field.path}`}
              >
                {field.label}
              </label>
              <input
                id={`instance-${field.path}`}
                className={styles.currencyInput}
                type="number"
                min="0"
                step={field.integer ? '1' : 'any'}
                value={String(values[field.path] ?? '')}
                onChange={(e) =>
                  setValues((prev) => ({
                    ...prev,
                    [field.path]: e.target.value,
                  }))
                }
              />
            </div>
          ))}
          {group.toggles.map((toggle) => (
            <div className={styles.row} key={toggle.path}>
              <FormControlLabel
                control={
                  <Switch
                    checked={Boolean(values[toggle.path])}
                    onChange={(e) =>
                      setValues((prev) => ({
                        ...prev,
                        [toggle.path]: e.target.checked,
                      }))
                    }
                    inputProps={{ 'aria-label': toggle.label }}
                  />
                }
                label={toggle.label}
              />
            </div>
          ))}
        </div>
      ))}
      <button
        type="button"
        className={styles.textButton}
        onClick={() => void handleSave()}
        disabled={!isDirty || saving}
      >
        {saving ? '保存中…' : '保存实例设置'}
      </button>
    </div>
  )
}

export function InstanceSettingsSection() {
  const { data, error: loadQueryError } = useQuery({
    queryKey: extraQueryKeys.instanceSettings(),
    queryFn: getInstanceSettings,
  })
  const loadError = toErrorMessage(loadQueryError)

  if (loadError) {
    return (
      <div className={styles.card}>
        <h3 className={styles.heading}>实例设置</h3>
        <p className={styles.error} role="alert">
          {loadError}
        </p>
      </div>
    )
  }

  if (!data) return null

  return <InstanceSettingsEditor initial={data} />
}
