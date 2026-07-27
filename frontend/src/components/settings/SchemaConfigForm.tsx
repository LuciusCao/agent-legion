import { FormControlLabel, MenuItem, Switch, TextField } from '@mui/material'
import type { ConfigSchema, ConfigSchemaProperty } from '../../types'

interface Props {
  schema: ConfigSchema
  values: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
  disabled?: boolean
}

function helperText(prop: ConfigSchemaProperty): string | undefined {
  const parts: string[] = []
  if (prop.description) parts.push(prop.description)
  if (prop.minimum != null || prop.maximum != null) {
    const min = prop.minimum != null ? String(prop.minimum) : '-∞'
    const max = prop.maximum != null ? String(prop.maximum) : '+∞'
    parts.push(`范围: ${min} ~ ${max}`)
  }
  return parts.length > 0 ? parts.join(' · ') : undefined
}

function placeholder(prop: ConfigSchemaProperty): string | undefined {
  return prop.default != null ? String(prop.default) : undefined
}

export function SchemaConfigForm({
  schema,
  values,
  onChange,
  disabled,
}: Props) {
  const properties = schema.properties ?? {}
  const keys = Object.keys(properties)
  if (keys.length === 0) {
    return <div style={{ fontSize: 12, color: '#616161' }}>无可配置参数</div>
  }

  const required = new Set(schema.required ?? [])

  const setValue = (key: string, value: unknown) => {
    const next = { ...values }
    if (value === '' || value === undefined || value === null) {
      delete next[key]
    } else {
      next[key] = value
    }
    onChange(next)
  }

  const renderField = (key: string, prop: ConfigSchemaProperty) => {
    const value = values[key]

    if (prop.enum) {
      return (
        <TextField
          key={key}
          select
          label={key}
          variant="outlined"
          required={required.has(key)}
          disabled={disabled}
          value={value ?? ''}
          onChange={(event) => setValue(key, event.target.value)}
          helperText={helperText(prop)}
          fullWidth
        >
          <MenuItem value="">（默认）</MenuItem>
          {prop.enum.map((option) => (
            <MenuItem key={String(option)} value={option}>
              {String(option)}
            </MenuItem>
          ))}
        </TextField>
      )
    }

    if (prop.type === 'boolean') {
      return (
        <FormControlLabel
          key={key}
          control={
            <Switch
              checked={Boolean(value)}
              disabled={disabled}
              onChange={(event) => setValue(key, event.target.checked)}
            />
          }
          label={key}
        />
      )
    }

    if (prop.type === 'integer' || prop.type === 'number') {
      return (
        <TextField
          key={key}
          type="number"
          label={key}
          variant="outlined"
          required={required.has(key)}
          disabled={disabled}
          placeholder={placeholder(prop)}
          value={value ?? ''}
          onChange={(event) => {
            const raw = event.target.value
            if (raw === '') {
              setValue(key, '')
              return
            }
            const num = Number(raw)
            if (!Number.isNaN(num)) setValue(key, num)
          }}
          inputProps={{ min: prop.minimum, max: prop.maximum }}
          helperText={helperText(prop)}
          fullWidth
        />
      )
    }

    return (
      <TextField
        key={key}
        type={prop.secret ? 'password' : 'text'}
        label={key}
        variant="outlined"
        required={required.has(key)}
        disabled={disabled}
        placeholder={placeholder(prop)}
        value={value ?? ''}
        onChange={(event) => setValue(key, event.target.value)}
        helperText={helperText(prop)}
        fullWidth
      />
    )
  }

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {keys.map((key) => renderField(key, properties[key]))}
    </div>
  )
}
