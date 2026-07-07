import { useState } from 'react'
import { ArtifactPopover } from './ArtifactPopover'
import styles from './DagNodeChips.module.css'

const CHIP_LIMIT = 3
type ChipListProps = {
  title: string
  items: string[]
  variant: 'in' | 'out'
}
export function ChipList({ title, items, variant }: ChipListProps) {
  const [openArtifactList, setOpenArtifactList] = useState(false)
  const visible = items.slice(0, CHIP_LIMIT)
  const hidden = Math.max(items.length - visible.length, 0)

  if (items.length === 0) return null

  return (
    <div className={styles.chipGroup}>
      <div className={styles.chipTitle}>
        {title}（{items.length}）
      </div>
      <div className={styles.chipRow}>
        {visible.map((item) => (
          <span
            key={item}
            className={`${styles.chip} ${variant === 'out' ? styles.chipOut : ''}`}
            title={item}
          >
            {item.length > 18 ? item.slice(0, 17) + '…' : item}
          </span>
        ))}
        {hidden > 0 && (
          <button
            type="button"
            className={styles.moreButton}
            aria-label={`显示其余 ${hidden} 个${title}产物`}
            onClick={(event) => {
              event.stopPropagation()
              setOpenArtifactList(true)
            }}
          >
            +{hidden}
          </button>
        )}
      </div>
      {openArtifactList && (
        <ArtifactPopover
          items={items}
          onClose={() => {
            setOpenArtifactList(false)
          }}
        />
      )}
    </div>
  )
}
