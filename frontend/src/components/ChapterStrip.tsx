import type { Chapter } from '../types'
import { Chip } from '@mui/material'
import styles from './ChapterStrip.module.css'

interface ChapterStripProps {
  chapters: Chapter[]
  currentTime: number
  onSeek: (time: number) => void
}

export function ChapterStrip({
  chapters,
  currentTime,
  onSeek,
}: ChapterStripProps) {
  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = Math.floor(seconds % 60)
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  return (
    <div className={styles.chaptersStrip}>
      <span className={styles.chapterLabel}>章节</span>
      {chapters.map((chapter, index) => {
        const isActive =
          currentTime >= chapter.start &&
          currentTime < (chapters[index + 1]?.start ?? Infinity)
        return (
          <Chip
            key={index}
            label={`${formatTime(chapter.start)} ${chapter.title}`}
            onClick={() => onSeek(chapter.start)}
            variant={isActive ? 'filled' : 'outlined'}
            sx={
              isActive
                ? {
                    backgroundColor: '#000000',
                    color: '#ffffff',
                    '&:hover': { backgroundColor: '#333333' },
                  }
                : {}
            }
          />
        )
      })}
    </div>
  )
}
