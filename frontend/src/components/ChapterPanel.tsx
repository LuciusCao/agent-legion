import { List, ListItemButton, ListItemText } from '@mui/material'
import { useArtifactStore } from '../stores/artifactStore'

export function ChapterPanel({ onSeek }: { onSeek: (time: number) => void }) {
  const { artifacts } = useArtifactStore()
  const chapters = artifacts.chapters

  if (!chapters.length) {
    return <div className="empty-state">暂无章节</div>
  }

  return (
    <List className="tab-panel" dense>
      {chapters.map((chapter, index) => (
        <ListItemButton
          key={chapter.id || index}
          onClick={() => onSeek(chapter.start)}
        >
          <ListItemText
            primary={
              <span
                style={{
                  fontVariantNumeric: 'tabular-nums',
                  minWidth: '80px',
                }}
              >
                {formatTime(chapter.start)}
              </span>
            }
            secondary={chapter.title}
          />
        </ListItemButton>
      ))}
    </List>
  )
}

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
