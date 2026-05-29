import { useArtifactStore } from '../stores/artifactStore'

export function ChapterPanel({ onSeek }: { onSeek: (time: number) => void }) {
  const { artifacts } = useArtifactStore()
  const chapters = artifacts.chapters

  if (!chapters.length) {
    return <div className="empty-state">暂无章节</div>
  }

  return (
    <md-list className="tab-panel">
      {chapters.map((chapter, index) => (
        <md-list-item
          key={chapter.id || index}
          type="button"
          onClick={() => onSeek(chapter.start)}
        >
          <div
            slot="headline"
            style={{ fontVariantNumeric: 'tabular-nums', minWidth: '80px' }}
          >
            {formatTime(chapter.start)}
          </div>
          <div slot="supporting-text">{chapter.title}</div>
        </md-list-item>
      ))}
    </md-list>
  )
}

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
