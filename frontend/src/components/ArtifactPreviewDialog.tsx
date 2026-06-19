import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import styles from './ArtifactPreviewDialog.module.css'

export interface ArtifactPreviewDialogProps {
  open: boolean
  name: string
  content: string
  onClose: () => void
}

function formatContent(name: string, content: string): string {
  if (name.endsWith('.json')) {
    try {
      return JSON.stringify(JSON.parse(content), null, 2)
    } catch {
      return content
    }
  }
  return content
}

export function ArtifactPreviewDialog({
  open,
  name,
  content,
  onClose,
}: ArtifactPreviewDialogProps) {
  if (!open) return null

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: '900px', width: '95vw' } }}
    >
      <DialogTitle>{name}</DialogTitle>
      <DialogContent className={styles.content}>
        <pre className={styles.pre}>{formatContent(name, content)}</pre>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose}>
          关闭
        </Button>
      </DialogActions>
    </Dialog>
  )
}
