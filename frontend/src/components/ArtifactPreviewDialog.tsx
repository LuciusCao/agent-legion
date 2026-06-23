import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { JsonTree } from './JsonTree'
import styles from './ArtifactPreviewDialog.module.css'

export interface ArtifactPreviewDialogProps {
  open: boolean
  name: string
  content: string
  onClose: () => void
}

// prettier-ignore
const tryParseJson = (content: string): unknown | null => { try { return JSON.parse(content) } catch { return null } }

export function ArtifactPreviewDialog({
  open,
  name,
  content,
  onClose,
}: ArtifactPreviewDialogProps) {
  if (!open) return null

  const parsedJson = name.endsWith('.json') ? tryParseJson(content) : null

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: '900px', width: '95vw' } }}
    >
      <DialogTitle>{name}</DialogTitle>
      <DialogContent className={styles.content}>
        {parsedJson !== null ? (
          <JsonTree data={parsedJson} />
        ) : (
          <pre className={styles.pre}>{content}</pre>
        )}
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose}>
          关闭
        </Button>
      </DialogActions>
    </Dialog>
  )
}
