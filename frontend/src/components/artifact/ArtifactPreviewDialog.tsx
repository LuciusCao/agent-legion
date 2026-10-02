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

export function ArtifactPreviewDialog(props: ArtifactPreviewDialogProps) {
  if (!props.open) return null
  // 预览/源码模式存在内层：关闭即卸载、按 name 重挂载，重开或换产物一律
  // 回到默认的 rendered 态，状态不在产物之间泄漏（#777 review）。
  return <OpenArtifactPreviewDialog key={props.name} {...props} />
}

function OpenArtifactPreviewDialog({
  open,
  name,
  content,
  onClose,
}: ArtifactPreviewDialogProps) {
  const [mode, setMode] = useState<ArtifactPreviewMode>('rendered')

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
