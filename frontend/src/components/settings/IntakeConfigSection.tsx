import { Button, Checkbox, MenuItem, TextField } from '@mui/material'
import styles from '../../pages/SettingsPage.module.css'
import { ConnectionTestStatus } from './ConnectionTestStatus'
import type { WorkflowDefinitionRecord, WorkspaceSettings } from '../../types'
import type { TestStatus } from '../../stores/settingStore'

interface Props {
  settings: WorkspaceSettings
  workflowDefinition: WorkflowDefinitionRecord | null
  testStatus: TestStatus
  saveError: string | null
  isTesting: boolean
  isSaving: boolean
  setSettings: (s: Partial<WorkspaceSettings>) => void
  onTestConnection: () => void
}

export function IntakeConfigSection({
  settings,
  workflowDefinition,
  testStatus,
  saveError,
  isTesting,
  isSaving,
  setSettings,
  onTestConnection,
}: Props) {
  const toggleIntakeMode = (key: string) => {
    const isEnabled = settings.intakeModes.includes(key)
    const nextModes = isEnabled
      ? settings.intakeModes.filter((k) => k !== key)
      : [...settings.intakeModes, key]
    setSettings({ intakeModes: nextModes })
  }

  return (
    <section id="intake-config" className={styles.section}>
      <h2 className={styles.sectionTitle}>接入与资源</h2>
      <hr className={styles.sectionDivider} />
      <div className={styles.field}>
        <TextField
          select
          label="默认实体类型"
          variant="outlined"
          value={settings.entityType}
          onChange={(e) =>
            setSettings({
              entityType: e.target.value as 'question' | 'knowledge' | 'video',
            })
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
            const isChecked = settings.intakeModes.includes(mode.key)
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
                  onChange={() => toggleIntakeMode(mode.key)}
                />
                <span style={{ fontSize: 14 }}>{mode.label}</span>
              </div>
            )
          })}
        </div>
      </div>

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
