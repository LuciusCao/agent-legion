import { FormControlLabel, Switch } from '@mui/material'
import type {
  InstanceSettingsResponse,
  InstanceSettingsUpdate,
} from '../../api/instanceSettings'
import { fieldDef } from './instanceSettingsFields'
import type { FieldGroup } from './instanceSettingsFields'
import styles from '../GlobalSettingsPage.module.css'

// 实例设置表单的值转换/校验与字段组渲染（从 InstanceSettingsSection 拆出
// 以控制体积预算）。

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
    },
  }
}

export function FieldGroupFields({
  group,
  values,
  onChange,
}: {
  group: FieldGroup
  values: FormValues
  onChange: (path: string, value: string | boolean) => void
}) {
  return (
    <>
      {group.fields.map((field) => (
        <div className={styles.row} key={field.path}>
          <label className={styles.label} htmlFor={`instance-${field.path}`}>
            {field.label}
          </label>
          <input
            id={`instance-${field.path}`}
            className={styles.currencyInput}
            type="number"
            min="0"
            max={field.max}
            step={field.integer ? '1' : 'any'}
            value={String(values[field.path] ?? '')}
            onChange={(e) => onChange(field.path, e.target.value)}
          />
          {field.hint && <span className={styles.hint}>{field.hint}</span>}
        </div>
      ))}
      {group.toggles.map((toggle) => (
        <div className={styles.row} key={toggle.path}>
          <FormControlLabel
            control={
              <Switch
                checked={Boolean(values[toggle.path])}
                onChange={(e) => onChange(toggle.path, e.target.checked)}
                inputProps={{ 'aria-label': toggle.label }}
              />
            }
            label={toggle.label}
          />
        </div>
      ))}
    </>
  )
}
