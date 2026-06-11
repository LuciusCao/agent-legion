import type { CSSProperties } from 'react'
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

  const dialogStyle = {
    '--md-dialog-container-color': '#ffffff',
    maxWidth: '900px',
    width: '95vw',
  } as CSSProperties

  return (
    <md-dialog open onClosed={onClose} style={dialogStyle}>
      <div slot="headline">{name}</div>
      <div slot="content" className={styles.content}>
        <pre className={styles.pre}>{formatContent(name, content)}</pre>
      </div>
      <div slot="actions">
        <md-text-button onClick={onClose}>关闭</md-text-button>
      </div>
    </md-dialog>
  )
}
