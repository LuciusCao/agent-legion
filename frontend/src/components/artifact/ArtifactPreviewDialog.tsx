import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import {
  ArtifactPreviewBody,
  ArtifactPreviewModeToggle,
  type ArtifactPreviewMode,
} from './ArtifactRenderedPreview'
import styles from './ArtifactPreviewDialog.module.css'

export interface ArtifactPreviewDialogProps {
  open: boolean
  name: string
  content: string
  onClose: () => void
}

export function ArtifactPreviewDialog({
  open,
  name,
  content,
  onClose,
}: ArtifactPreviewDialogProps) {
  const [mode, setMode] = useState<ArtifactPreviewMode>('rendered')

  if (!open) return null

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: '900px', width: '95vw' } }}
    >
      <DialogTitle className={styles.title}>
        <span className={styles.titleName}>{name}</span>
        <ArtifactPreviewModeToggle name={name} mode={mode} onMode={setMode} />
      </DialogTitle>
      <DialogContent className={styles.content}>
        <ArtifactPreviewBody
          name={name}
          content={content}
          mode={mode}
          preClassName={styles.pre}
        />
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose}>
          关闭
        </Button>
      </DialogActions>
    </Dialog>
  )
}
