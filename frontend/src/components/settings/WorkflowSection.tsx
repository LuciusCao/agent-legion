import { useEffect, useState } from 'react'
import { MenuItem, TextField } from '@mui/material'
import { fetchWorkflows } from '../../api'
import styles from '../../pages/SettingsPage.module.css'

interface Props {
  workflowKey: string
  onChange: (key: string) => void
}

export function WorkflowSection({ workflowKey, onChange }: Props) {
  const [options, setOptions] = useState<Array<{ key: string; label: string }>>(
    []
  )
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    fetchWorkflows()
      .then((data) => {
        setOptions(data.workflows.map((p) => ({ key: p.key, label: p.label })))
      })
      .catch(() => {
        setOptions([])
        setLoadError(true)
      })
  }, [])

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
        {loadError && (
          <div className="error-text" role="alert" style={{ marginTop: 12 }}>
            工作流列表加载失败，请刷新重试
          </div>
        )}
      </div>
    </section>
  )
}
