import { TextField } from '@mui/material'
import type {
  ConfigSchema,
  ResourceBinding,
  ResourceProviderDefinition,
} from '../../types'
import { SchemaConfigForm } from './SchemaConfigForm'

interface Props {
  provider: ResourceProviderDefinition
  binding: ResourceBinding
  onConfigChange: (config: Record<string, unknown>) => void
}

function hasSchemaProperties(schema: unknown): schema is ConfigSchema {
  return (
    typeof schema === 'object' &&
    schema !== null &&
    Object.keys((schema as ConfigSchema).properties ?? {}).length > 0
  )
}

export function ResourceProviderCard({
  provider,
  binding,
  onConfigChange,
}: Props) {
  const config = binding.config ?? {}

  const handleFallbackChange = (paramKey: string, value: string) => {
    const next = { ...config }
    if (value) {
      next[paramKey] = value
    } else {
      delete next[paramKey]
    }
    onConfigChange(next)
  }

  return (
    <div
      style={{
        border: '1px solid #e0e0e0',
        borderRadius: 12,
        padding: 16,
      }}
    >
      <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 4 }}>
        {provider.provider}
      </div>
      <div style={{ fontSize: 12, color: '#616161', marginBottom: 12 }}>
        Path: {provider.path}
      </div>
      {hasSchemaProperties(provider.config_schema) ? (
        <SchemaConfigForm
          schema={provider.config_schema}
          values={config}
          onChange={onConfigChange}
        />
      ) : (
        <div style={{ display: 'grid', gap: 8 }}>
          {provider.paramKeys.map((paramKey) => (
            <TextField
              key={paramKey}
              label={paramKey}
              variant="outlined"
              placeholder={provider.defaultParams[paramKey] || ''}
              value={(config[paramKey] as string | undefined) || ''}
              onChange={(event) =>
                handleFallbackChange(paramKey, event.target.value)
              }
              fullWidth
            />
          ))}
        </div>
      )}
    </div>
  )
}
