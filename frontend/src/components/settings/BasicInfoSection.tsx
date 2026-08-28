import { MenuItem, TextField } from '@mui/material'
import styles from '../../pages/SettingsPage.module.css'
import type { WorkspaceSettings } from '../../types'

interface Props {
  workspaceName: string
  workspaceDescription: string
  entityType: WorkspaceSettings['entityType']
  saveError: string | null
  onNameChange: (name: string) => void
  onDescriptionChange: (description: string) => void
  onEntityTypeChange: (entityType: WorkspaceSettings['entityType']) => void
}

export function BasicInfoSection({
  workspaceName,
  workspaceDescription,
  entityType,
  saveError,
  onNameChange,
  onDescriptionChange,
  onEntityTypeChange,
}: Props) {
  return (
    <section id="basic-info" className={styles.section}>
      <h2 className={styles.sectionTitle}>基本信息</h2>
      <hr className={styles.sectionDivider} />
      <div className={styles.field}>
        <TextField
          label="Workspace 名称"
          variant="outlined"
          value={workspaceName}
          onChange={(event) => onNameChange(event.target.value)}
          fullWidth
        />
      </div>
      <div className={styles.field}>
        <TextField
          label="描述"
          variant="outlined"
          multiline
          rows={2}
          value={workspaceDescription}
          onChange={(event) => onDescriptionChange(event.target.value)}
          fullWidth
        />
      </div>
      <div className={styles.field}>
        <TextField
          select
          label="默认实体类型"
          variant="outlined"
          value={entityType}
          onChange={(event) => onEntityTypeChange(event.target.value)}
          fullWidth
        >
          <MenuItem value="question">question</MenuItem>
          <MenuItem value="knowledge">knowledge</MenuItem>
          <MenuItem value="video">video</MenuItem>
        </TextField>
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
