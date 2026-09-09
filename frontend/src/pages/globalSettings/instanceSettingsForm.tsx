import { FormControlLabel, Switch } from '@mui/material'
import type { FieldGroup } from './instanceSettingsFields'
import type { FormValues } from './instanceSettingsPayload'
import styles from '../GlobalSettingsPage.module.css'

// 实例设置表单的字段组渲染（值转换/PUT 载荷在 instanceSettingsPayload.ts，
// 从此文件拆出以控制体积预算）。

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
