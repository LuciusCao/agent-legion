import { TextField } from '@mui/material'
import type { ConfigSchemaProperty } from '../../types'

export function configHelperText(
  prop: ConfigSchemaProperty
): string | undefined {
  const parts: string[] = []
  if (prop.description) parts.push(prop.description)
  if (prop.minimum != null || prop.maximum != null) {
    const min = prop.minimum != null ? String(prop.minimum) : '-∞'
    const max = prop.maximum != null ? String(prop.maximum) : '+∞'
    parts.push(`范围: ${min} ~ ${max}`)
  }
  return parts.length > 0 ? parts.join(' · ') : undefined
}

export function configPlaceholder(
  prop: ConfigSchemaProperty
): string | undefined {
  return prop.default != null ? String(prop.default) : undefined
}

/**
 * Write-only marker the backend returns for secret fields instead of the
 * stored value: the form only learns whether a value is set and re-entering
 * a new one overwrites it (VAULT-SECRET-001).
 */
function isSecretMarker(value: unknown): value is { secret_set: boolean } {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { secret_set?: unknown }).secret_set === 'boolean'
  )
}

interface Props {
  name: string
  prop: ConfigSchemaProperty
  value: unknown
  required: boolean
  disabled?: boolean
  onChange: (value: string) => void
}

/**
 * Secret config field: never echoes the stored value. A masked marker shows
 * 已设置/未设置; typing emits the raw string (including '' for an explicit
 * clear) so the backend can overwrite or delete the vault entry.
 */
export function SecretConfigField({
  name,
  prop,
  value,
  required,
  disabled,
  onChange,
}: Props) {
  const marker = isSecretMarker(value) ? value : undefined
  const statusText = marker
    ? marker.secret_set
      ? '已设置'
      : '未设置'
    : undefined
  const helper = [configHelperText(prop), statusText]
    .filter(Boolean)
    .join(' · ')
  return (
    <TextField
      type="password"
      label={name}
      variant="outlined"
      required={required}
      disabled={disabled}
      placeholder={
        marker?.secret_set ? '已设置,输入新值覆盖' : configPlaceholder(prop)
      }
      value={typeof value === 'string' ? value : ''}
      onChange={(event) => onChange(event.target.value)}
      helperText={helper || undefined}
      fullWidth
    />
  )
}
