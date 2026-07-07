import { useArtifactPopover } from '../hooks/useArtifactPopover'
import s from './ArtifactPopover.module.css'

export function ArtifactPopover({
  items,
  onClose,
}: {
  items: string[]
  onClose: () => void
}) {
  const ref = useArtifactPopover(onClose)
  return (
    <div
      ref={ref}
      className={s.artifactPopover}
      role="dialog"
      aria-label="产物列表"
    >
      <button
        className={s.popoverClose}
        onClick={(e) => (e.stopPropagation(), onClose())}
      >
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
