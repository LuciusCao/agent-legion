import CloseIcon from '@mui/icons-material/Close'
import { Fade, IconButton, Slide } from '@mui/material'
import { useAppBarBottom } from '../../hooks/useAppBarBottom'
import { useUiStore } from '../../stores/uiStore'
import { TokenUsageDialogContent } from './TokenUsageDialogContent'
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
  const { tokenUsageDialogOpen: open, setTokenUsageDialogOpen: setOpen } =
    useUiStore()
  const appBarBottom = useAppBarBottom()
  const title =
    scope === 'workspace' ? 'Workspace Token 使用分析' : 'Job Token 使用分析'
  const close = () => setOpen(false)

  return (
    <>
      <Fade in={open} mountOnEnter unmountOnExit>
        <div
          className={styles.backdrop}
          style={{ top: appBarBottom }}
          onClick={close}
        />
      </Fade>
      <Slide direction="down" in={open} mountOnEnter unmountOnExit>
        <div
          className={styles.panel}
          style={{ top: appBarBottom }}
          aria-label={title}
        >
          <div className={styles.header}>
            {title}
            <IconButton size="small" onClick={close}>
              <CloseIcon />
            </IconButton>
          </div>
          <div className={styles.content}>
            <TokenUsageDialogContent
              scope={scope}
              workspaceId={workspaceId}
              jobId={jobId}
            />
          </div>
        </div>
      </Slide>
    </>
  )
}
