import { useState } from 'react'
import styles from './ArtifactDrawer.module.css'

export interface Artifact {
  name: string
  size?: number
  content?: string
}

export interface ArtifactDrawerProps {
  open: boolean
  artifacts: Artifact[]
  onClose: () => void
  onDownload: (name: string) => void
}

function formatPreview(artifact: Artifact): string {
  if (!artifact.content) return ''
  if (artifact.name.endsWith('.json')) {
    try {
      return JSON.stringify(JSON.parse(artifact.content), null, 2)
    } catch {
      return artifact.content
    }
  }
  return artifact.content
}

export function ArtifactDrawer({
  open,
  artifacts,
  onClose,
  onDownload,
}: ArtifactDrawerProps) {
  const [selectedName, setSelectedName] = useState<string | null>(null)

  if (!open) return null

  const selectedArtifact = selectedName
    ? artifacts.find((a) => a.name === selectedName)
    : null

  return (
    <div
      className={styles.backdrop}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div className={styles.drawer} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h3>产物</h3>
          <button type="button" className={styles.closeBtn} onClick={onClose}>
            关闭
          </button>
        </div>

        {!selectedArtifact ? (
          <ul className={styles.list}>
            {artifacts.map((artifact) => (
              <li key={artifact.name} className={styles.item}>
                <button
                  type="button"
                  className={styles.nameBtn}
                  onClick={() => setSelectedName(artifact.name)}
                >
                  {artifact.name}
                </button>
                <button
                  type="button"
                  className={styles.downloadBtn}
                  onClick={() => onDownload(artifact.name)}
                >
                  下载
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div className={styles.previewPane}>
            <button
              type="button"
              className={styles.backBtn}
              onClick={() => setSelectedName(null)}
            >
              返回列表
            </button>
            <pre className={styles.preview}>
              {formatPreview(selectedArtifact)}
            </pre>
            {selectedArtifact.size !== undefined && (
              <div className={styles.meta}>
                大小: {selectedArtifact.size} bytes
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
