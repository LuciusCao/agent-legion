import { Button, Checkbox, MenuItem, TextField } from '@mui/material'
import styles from '../../pages/SettingsPage.module.css'
import { ConnectionTestStatus } from './ConnectionTestStatus'
import type {
  ResourceBinding,
  ResourceProviderDefinition,
  WorkflowDefinitionRecord,
} from '../../types'
import type { TestStatus } from '../../stores/settingStore'

interface Props {
  entityType: 'question' | 'knowledge' | 'video'
  onEntityTypeChange: (type: 'question' | 'knowledge' | 'video') => void
  intakeModes: string[]
  workflowDefinition: WorkflowDefinitionRecord | null
  settingsResources: Record<string, ResourceBinding>
  resourceProviders: ResourceProviderDefinition[]
  testStatus: TestStatus
  saveError: string | null
  isTesting: boolean
  isSaving: boolean
  onToggleIntakeMode: (key: string) => void
  onResourceConfigChange: (providerKey: string, paramKey: string, value: string) => void
  onTestConnection: () => void
}

export function IntakeConfigSection({
  entityType,
  onEntityTypeChange,
  intakeModes,
  workflowDefinition,
  settingsResources,
  resourceProviders,
  testStatus,
  saveError,
  isTesting,
  isSaving,
  onToggleIntakeMode,
  onResourceConfigChange,
  onTestConnection,
}: Props) {
  return (
    <section id="intake-config" className={styles.section}>
      <h2 className={styles.sectionTitle}>接入与资源</h2>
      <hr className={styles.sectionDivider} />
      <div className={styles.field}>
        <TextField
          select
          label="默认实体类型"
          variant="outlined"
          value={entityType}
          onChange={(e) =>
            onEntityTypeChange(e.target.value as 'question' | 'knowledge' | 'video')
          }
          fullWidth
        >
          <MenuItem value="question">question</MenuItem>
          <MenuItem value="knowledge">knowledge</MenuItem>
          <MenuItem value="video">video</MenuItem>
        </TextField>
      </div>

      <div className={styles.field}>
        <span
          style={{
            fontSize: 12,
            color: '#616161',
          }}
        >
          接入模式
        </span>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            marginTop: 8,
          }}
        >
          {(workflowDefinition?.intake?.modes || []).map((mode) => {
            const isChecked = intakeModes.includes(mode.key)
            return (
              <div
                key={mode.key}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <Checkbox
                  checked={isChecked}
                  onChange={() => onToggleIntakeMode(mode.key)}
                />
                <span style={{ fontSize: 14 }}>{mode.label}</span>
              </div>
            )
          })}
        </div>
      </div>

      {(() => {
        const activeKeys = new Set<string>()
        for (const mode of workflowDefinition?.intake?.modes || []) {
          if (intakeModes.includes(mode.key) && mode.resource) {
            activeKeys.add(mode.resource)
          }
        }
        if (activeKeys.size === 0) return null
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <span
              style={{
                fontSize: 12,
                color: '#616161',
              }}
            >
              资源接口参数
            </span>
            {resourceProviders
              .filter((p) => activeKeys.has(p.key))
              .map((provider) => {
                const binding = settingsResources[provider.key] || {
                  enabled: true,
                  config: {},
                }
                return (
                  <div
                    key={provider.key}
                    style={{
                      border: '1px solid #e0e0e0',
                      borderRadius: 12,
                      padding: 16,
                    }}
                  >
                    <div
                      style={{
                        fontWeight: 500,
                        fontSize: 14,
                        marginBottom: 4,
                      }}
                    >
                      {provider.provider}
                    </div>
                    <div
                      style={{
                        fontSize: 12,
                        color: '#616161',
                        marginBottom: 12,
                      }}
                    >
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
                          onChange={(event) =>
                            onResourceConfigChange(
                              provider.key,
                              paramKey,
                              event.target.value
                            )
                          }
                          fullWidth
                        />
                      ))}
                    </div>
                  </div>
                )
              })}
          </div>
        )
      })()}

      <div
        style={{
          display: 'flex',
          gap: 12,
          flexWrap: 'wrap',
          marginTop: 16,
        }}
      >
        <Button
          variant="outlined"
          onClick={onTestConnection}
          disabled={isTesting || isSaving}
        >
          {isTesting ? '测试中...' : '测试连接'}
        </Button>
        <div aria-live="polite" aria-atomic="true">
          <ConnectionTestStatus
            state={testStatus.state}
            message={testStatus.message}
          />
        </div>
      </div>
      {saveError && (
        <div
          className="error-text"
          role="alert"
          style={{ color: '#d32f2f', marginTop: 12 }}
        >
          {saveError}
        </div>
      )}
    </section>
  )
}
