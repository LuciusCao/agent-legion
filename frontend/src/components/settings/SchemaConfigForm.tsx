import { FormControlLabel, MenuItem, Switch, TextField } from '@mui/material'
import type { ConfigSchema, ConfigSchemaProperty } from '../../types'
import {
  SecretConfigField,
  configHelperText,
  configPlaceholder,
} from './SecretConfigField'
import {
  ConnectionKeyDatalist,
  connectionListProp,
  useConnectionOptions,
} from './connectionOptions'

interface Props {
  schema: ConfigSchema
  values: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
  disabled?: boolean
}

export function SchemaConfigForm({
  schema,
  values,
  onChange,
  disabled,
}: Props) {
  const properties = schema.properties ?? {}
  const keys = Object.keys(properties)
  const { datalistId, options: connectionOptions } = useConnectionOptions(
    properties['connection']
  )
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

    if (prop.secret) {
      // Secret values stay write-only: the raw string (including '') reaches
      // the backend so an explicit clear does not drop the key.
      return (
        <SecretConfigField
          key={key}
          name={key}
          prop={prop}
          value={value}
          required={required.has(key)}
          disabled={disabled}
          onChange={(next) => onChange({ ...values, [key]: next })}
        />
      )
    }

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
          helperText={configHelperText(prop)}
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
          placeholder={configPlaceholder(prop)}
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
          helperText={configHelperText(prop)}
          fullWidth
        />
      )
    }

    return (
      <TextField
        key={key}
        type="text"
        label={key}
        variant="outlined"
        required={required.has(key)}
        disabled={disabled}
        placeholder={configPlaceholder(prop)}
        value={value ?? ''}
        onChange={(event) => setValue(key, event.target.value)}
        helperText={configHelperText(prop)}
        inputProps={connectionListProp(key, datalistId)}
        fullWidth
      />
    )
  }

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {keys.map((key) => renderField(key, properties[key]))}
      <ConnectionKeyDatalist id={datalistId} options={connectionOptions} />
    </div>
  )
}
