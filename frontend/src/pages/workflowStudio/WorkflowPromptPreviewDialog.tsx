import CloseIcon from '@mui/icons-material/Close'
import { Dialog, DialogContent, DialogTitle, IconButton } from '@mui/material'
import styles from './WorkflowPromptPreviewDialog.module.css'

export function WorkflowPromptPreviewDialog(props: {
  open: boolean
  nodeLabel: string
  prompt: string
  onClose: () => void
}) {
  return (
    <Dialog open={props.open} onClose={props.onClose} fullWidth maxWidth="md">
      <DialogTitle>
        {props.nodeLabel} · 运行 Prompt
        <IconButton aria-label="关闭 Prompt 预览" onClick={props.onClose}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent className={styles.dialogContent} dividers>
        <pre className={styles.prompt}>{props.prompt}</pre>
      </DialogContent>
    </Dialog>
  )
}
