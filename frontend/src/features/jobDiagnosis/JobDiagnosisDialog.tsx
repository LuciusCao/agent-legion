import { Dialog, DialogContent, DialogTitle, IconButton } from '@mui/material'
import { MaterialIcon } from '../../components/MaterialIcon'
import type { JobDiagnosisTarget } from './jobDiagnosisContext'
import { JobDiagnosisPanel } from './JobDiagnosisPanel'
import styles from './JobDiagnosisPanel.module.css'

type Props = {
  open: boolean
  target: JobDiagnosisTarget
  onClose: () => void
}

/** 排查对话的弹窗外壳（#329）。open 时才挂载面板——每次打开都是一个全新的
 * 排查会话（自动建会话 + 自动发上下文 primer），关闭即销毁本地状态。 */
export function JobDiagnosisDialog({ open, target, onClose }: Props) {
  const title = target.nodeLabel
    ? `排查：${target.nodeLabel}`
    : `排查：${target.jobTitle || target.jobId}`
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      aria-label={title}
    >
      <DialogTitle className={styles.dialogTitle}>
        <span>{title}</span>
        <IconButton aria-label="关闭" onClick={onClose} size="small">
          <MaterialIcon name="close" />
        </IconButton>
      </DialogTitle>
      <DialogContent className={styles.dialogContent}>
        {open && (
          <JobDiagnosisPanel workspaceId={target.workspaceId} target={target} />
        )}
      </DialogContent>
    </Dialog>
  )
}
