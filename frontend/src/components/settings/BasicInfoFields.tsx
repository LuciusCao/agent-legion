import { MenuItem, TextField } from '@mui/material'
import styles from '../../pages/SettingsPage.module.css'
import type { WorkspaceSettings } from '../../types'

/** 默认实体类型选择器（自 BasicInfoSection 拆出，文件预算）。 */
export function EntityTypeSelect(props: {
  value: WorkspaceSettings['entityType']
  onChange: (entityType: WorkspaceSettings['entityType']) => void
}) {
  return (
    <div className={styles.field}>
      <TextField
        select
        label="默认实体类型"
        variant="outlined"
        value={props.value}
        onChange={(event) => props.onChange(event.target.value)}
        fullWidth
      >
        <MenuItem value="question">question</MenuItem>
        <MenuItem value="knowledge">knowledge</MenuItem>
        <MenuItem value="video">video</MenuItem>
      </TextField>
    </div>
  )
}

/** 保存失败提示；无错误时不渲染。 */
export function SaveErrorAlert(props: { message: string | null }) {
  if (!props.message) return null
  return (
    <div
      className="error-text"
      role="alert"
      style={{ color: '#d32f2f', marginTop: 12 }}
    >
      {props.message}
    </div>
  )
}
