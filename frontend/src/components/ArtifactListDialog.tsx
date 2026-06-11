import type { CSSProperties } from 'react'
import styles from './ArtifactListDialog.module.css'

export interface ArtifactListDialogProps {
  open: boolean
  artifacts: string[]
  onClose: () => void
  onSelect: (name: string) => void
}

export function ArtifactListDialog({
  open,
  artifacts,
  onClose,
  onSelect,
}: ArtifactListDialogProps) {
  if (!open) return null

  const dialogStyle = {
    '--md-dialog-container-color': '#ffffff',
    maxWidth: '480px',
    width: '90vw',
  } as CSSProperties

  return (
    <md-dialog open onClosed={onClose} style={dialogStyle}>
      <div slot="headline">产物文件</div>
      <div slot="content">
        {artifacts.length === 0 ? (
          <p className={styles.empty}>暂无产物文件</p>
        ) : (
          <ul className={styles.list}>
            {artifacts.map((name) => (
              <li key={name}>
                <button
                  type="button"
                  className={styles.nameBtn}
                  onClick={() => onSelect(name)}
                >
                  {name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div slot="actions">
        <md-text-button onClick={onClose}>关闭</md-text-button>
      </div>
    </md-dialog>
  )
}
