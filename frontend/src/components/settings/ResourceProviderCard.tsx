import { TextField } from '@mui/material'
import type { ResourceBinding, ResourceProviderDefinition } from '../../types'

interface Props {
  provider: ResourceProviderDefinition
  binding: ResourceBinding
  onChange: (paramKey: string, value: string) => void
}

export function ResourceProviderCard({ provider, binding, onChange }: Props) {
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
      <div style={{ display: 'grid', gap: 8 }}>
        {provider.paramKeys.map((paramKey) => (
          <TextField
            key={paramKey}
            label={paramKey}
            variant="outlined"
            placeholder={provider.defaultParams[paramKey] || ''}
            value={binding.config[paramKey] || ''}
            onChange={(event) => onChange(paramKey, event.target.value)}
            fullWidth
          />
        ))}
      </div>
    </div>
  )
}
