import React, { useCallback } from 'react'
import { Chip } from '@mui/material'
import type { Chapter, InteractionNode } from '../types'
import { INTERACTION_TYPE_LABELS } from '../labels'
import { parseTimeSeconds } from '../lib/formatters'
import styles from './TimelineStrip.module.css'

interface TimelineStripProps {
  chapters: Chapter[]
  interactions: InteractionNode[]
  currentTime: number
  onSeek: (time: number) => void
  onReplayInteraction?: (index: number) => void
}

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export const TimelineStrip = React.memo(function TimelineStrip({
  chapters,
  interactions,
  currentTime,
  onSeek,
  onReplayInteraction,
}: TimelineStripProps) {
  const seekInteraction = useCallback(
    (time: number, index: number) => {
      onSeek(time)
      onReplayInteraction?.(index)
    },
    [onSeek, onReplayInteraction]
  )

  const getInteractionLabel = useCallback((node: InteractionNode) => {
    const type = String(node.type ?? '')
    return INTERACTION_TYPE_LABELS[type] || type || '交互节点'
  }, [])

  return (
    <div className={styles.timelineContainer}>
      <div className={styles.chipLine}>
        <span className={styles.rowLabel}>章节</span>
        <div className={styles.chipScroller}>
          {chapters.length === 0 && (
            <span className={styles.emptyHint}>暂无章节</span>
          )}
          {chapters.map((chapter, index) => {
            const isActive =
              currentTime >= chapter.start &&
              currentTime < (chapters[index + 1]?.start ?? Infinity)
            return (
              <Chip
                key={chapter.id ?? index}
                className={isActive ? styles.activeChapterChip : ''}
                label={`${formatTime(chapter.start)} ${chapter.title}`}
                onClick={() => onSeek(chapter.start)}
                size="small"
                variant="outlined"
              />
            )
          })}
        </div>
      </div>

      <div className={styles.chipLine}>
        <span className={styles.rowLabel}>互动</span>
        <div className={styles.chipScroller}>
          {interactions.length === 0 && (
            <span className={styles.emptyHint}>暂无互动</span>
          )}
          {interactions.map((node, index) => {
            const time = parseTimeSeconds(node.trigger_time ?? 0)
            const isActive = currentTime >= time && currentTime < time + 1.5
            return (
              <Chip
                key={node.id ?? index}
                className={
                  isActive
                    ? styles.activeInteractionChip
                    : styles.interactionChip
                }
                label={`${formatTime(time)} ${getInteractionLabel(node)}`}
                onClick={() => seekInteraction(time, index)}
                title={`${formatTime(time)} ${node.instruction || '交互节点'}`}
                size="small"
                variant="outlined"
              />
            )
          })}
        </div>
      </div>
    </div>
  )
})
