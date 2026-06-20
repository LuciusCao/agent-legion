import { MenuItem, TextField } from '@mui/material'
import styles from '../../pages/SettingsPage.module.css'

interface Props {
  workflowKey: string
  options: Array<{ key: string; label: string }>
  onChange: (key: string) => void
}

export function WorkflowSection({ workflowKey, options, onChange }: Props) {
  return (
    <section id="workflow" className={styles.section}>
      <h2 className={styles.sectionTitle}>工作流</h2>
      <hr className={styles.sectionDivider} />
      <div className={styles.field}>
        <TextField
          select
          label="工作流"
          variant="outlined"
          value={workflowKey || ''}
          onChange={(e) => onChange(e.target.value)}
          fullWidth
        >
          <MenuItem value="">请选择</MenuItem>
          {options.map((p) => (
            <MenuItem key={p.key} value={p.key}>
              {p.label}
            </MenuItem>
          ))}
        </TextField>
      </div>
    </section>
  )
}
