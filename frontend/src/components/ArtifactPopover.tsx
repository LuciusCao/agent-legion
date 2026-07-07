import React from 'react'
import styles from './ArtifactPopover.module.css'

export function ArtifactPopover({
  items,
  onClose,
}: {
  items: string[]
  onClose: (event: React.MouseEvent<HTMLButtonElement>) => void
}) {
  return (
    <div
      className={styles.artifactPopover}
      role="dialog"
      aria-label="完整产物列表"
    >
      <button type="button" className={styles.popoverClose} onClick={onClose}>
        关闭
      </button>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}
