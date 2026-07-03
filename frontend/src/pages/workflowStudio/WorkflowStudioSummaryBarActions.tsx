import { Button } from '@mui/material'
import styles from './WorkflowStudioSummaryBar.module.css'

type Props = {
  actionState: 'idle' | 'validating' | 'publishing'
  canSubmit: boolean
  canPublish: boolean
  dirty: boolean
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
}

export function WorkflowStudioSummaryBarActions({
  actionState,
  canSubmit,
  canPublish,
  dirty,
  onValidate,
  onPublish,
  onReset,
}: Props) {
  const busy = actionState !== 'idle'
  return (
    <div className={styles.actions}>
      <Button
        variant="outlined"
        size="small"
        onClick={onValidate}
        disabled={!canSubmit || busy}
      >
        {actionState === 'validating' ? '校验中' : '校验'}
      </Button>
      <Button
        variant="contained"
        size="small"
        onClick={onPublish}
        disabled={!canPublish || busy}
      >
        {actionState === 'publishing' ? '发布中' : '发布'}
      </Button>
      <Button
        variant="outlined"
        size="small"
        onClick={onReset}
        disabled={!dirty || busy}
      >
        重置
      </Button>
    </div>
  )
}
