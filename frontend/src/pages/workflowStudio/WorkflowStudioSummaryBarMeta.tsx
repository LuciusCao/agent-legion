import { Chip } from '@mui/material'
import type { WorkflowRevisionSummary } from '../../types'
import styles from './WorkflowStudioSummaryBar.module.css'

type Props = { revision: WorkflowRevisionSummary | null; dirty: boolean }

export function WorkflowStudioSummaryBarMeta({ revision, dirty }: Props) {
  const hash = revision?.definition_hash?.slice(0, 8) ?? '--------'
  return (
    <div className={styles.meta}>
      <Chip
        label={revision ? `v${revision.version}` : '无 active revision'}
        size="small"
        variant="outlined"
      />
      <Chip label={hash} size="small" variant="outlined" />
      <Chip
        label={dirty ? '有未保存修改' : '已同步'}
        size="small"
        color={dirty ? 'warning' : 'success'}
        variant={dirty ? 'filled' : 'outlined'}
      />
    </div>
  )
}
