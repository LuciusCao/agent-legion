import CodeIcon from '@mui/icons-material/Code'
import DifferenceIcon from '@mui/icons-material/Difference'
import { Button } from '@mui/material'
import styles from './WorkflowStudioCommandBar.module.css'

export function WorkflowStudioGlobalActions(props: {
  onOpenChanges: () => void
  onOpenYaml: () => void
}) {
  return (
    <div className={styles.globalActions}>
      <Button
        size="small"
        startIcon={<DifferenceIcon />}
        onClick={props.onOpenChanges}
      >
        <span className={styles.globalActionLabel}>查看变更</span>
      </Button>
      <Button size="small" startIcon={<CodeIcon />} onClick={props.onOpenYaml}>
        <span className={styles.globalActionLabel}>YAML 高级编辑</span>
      </Button>
    </div>
  )
}
