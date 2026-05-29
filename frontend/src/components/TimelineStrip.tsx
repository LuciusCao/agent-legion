import type { Chapter, InteractionNode } from '../types'
import { INTERACTION_TYPE_LABELS } from '../labels'
import { parseTimeSeconds } from '../helpers'
import styles from './TimelineStrip.module.css'

interface TimelineStripProps {
  chapters: Chapter[]
  interactions: InteractionNode[]
  currentTime: number
  onSeek: (time: number) => void
  onReplayInteraction?: (index: number) => void
}

export function TimelineStrip({
  chapters,
  interactions,
  currentTime,
  onSeek,
  onReplayInteraction,
}: TimelineStripProps) {
  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = Math.floor(seconds % 60)
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  const seekInteraction = (time: number, index: number) => {
    onSeek(time)
    onReplayInteraction?.(index)
  }

  const getInteractionLabel = (node: InteractionNode) => {
    const type = String(node.type ?? '')
    return INTERACTION_TYPE_LABELS[type] || type || '交互节点'
  }

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
              <md-suggestion-chip
                key={chapter.id ?? index}
                class={isActive ? styles.activeChapterChip : ''}
                label={`${formatTime(chapter.start)} ${chapter.title}`}
                onClick={() => onSeek(chapter.start)}
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
              <md-suggestion-chip
                key={node.id ?? index}
                class={
                  isActive
                    ? styles.activeInteractionChip
                    : styles.interactionChip
                }
                label={`${formatTime(time)} ${getInteractionLabel(node)}`}
                onClick={() => seekInteraction(time, index)}
                title={`${formatTime(time)} ${node.instruction || '交互节点'}`}
              />
            )
          })}
        </div>
      </div>
    </div>
  )
}
