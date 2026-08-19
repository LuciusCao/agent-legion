import { TextField } from '@mui/material'
import styles from '../../pages/SettingsPage.module.css'

interface Props {
  workflowKey: string
  onChange: (key: string) => void
}

/**
 * 设置页工作流 section（schema v50）：workflow key 只是 workspace 上的普通
 * 标识，不再有全局 catalog 可选项，改为自由文本。改成新 key 后第一次
 * 发布会成为该 key 的 revision v1；已发布 key 的 revision 历史不受影响。
 */
export function WorkflowSection({ workflowKey, onChange }: Props) {
  return (
    <section id="workflow" className={styles.section}>
      <h2 className={styles.sectionTitle}>工作流</h2>
      <hr className={styles.sectionDivider} />
      <div className={styles.field}>
        <TextField
          label="工作流 Key"
          variant="outlined"
          value={workflowKey || ''}
          onChange={(e) => onChange(e.target.value)}
          placeholder="snake_case，例如 my_pipeline"
          helperText="工作流是 workspace 内部的一份 DAG；修改 key 后需在 Studio 发布对应定义"
          fullWidth
        />
      </div>
    </section>
  )
}
