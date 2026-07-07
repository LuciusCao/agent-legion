import { Dialog, DialogContent, DialogTitle, IconButton } from '@mui/material'
import { useUiStore } from '../stores/uiStore'
import { TokenUsagePanel } from './TokenUsagePanel'
import { TokenUsageJobPanel } from './TokenUsageJobPanel'
import { MaterialIcon } from './MaterialIcon'
import styles from './TokenUsageDialog.module.css'

interface TokenUsageDialogProps {
  scope: 'workspace' | 'job'
  workspaceId?: string
  jobId?: string
}

export function TokenUsageDialog({
  scope,
  workspaceId,
  jobId,
}: TokenUsageDialogProps) {
  const { tokenUsageDialogOpen, setTokenUsageDialogOpen } = useUiStore()

  const title =
    scope === 'workspace' ? 'Workspace Token 使用分析' : 'Job Token 使用分析'

  return (
    <Dialog
      open={tokenUsageDialogOpen}
      onClose={() => setTokenUsageDialogOpen(false)}
      fullWidth
      maxWidth={false}
      classes={{ paper: styles.paper }}
      aria-labelledby="token-usage-dialog-title"
    >
      <DialogTitle id="token-usage-dialog-title" className={styles.title}>
        {title}
        <IconButton
          size="small"
          aria-label="关闭"
          onClick={() => setTokenUsageDialogOpen(false)}
        >
          <MaterialIcon name="close" />
        </IconButton>
      </DialogTitle>
      <DialogContent className={styles.content}>
        {scope === 'workspace' && workspaceId && (
          <TokenUsagePanel workspaceId={workspaceId} />
        )}
        {scope === 'job' && jobId && <TokenUsageJobPanel jobId={jobId} />}
      </DialogContent>
    </Dialog>
  )
}
