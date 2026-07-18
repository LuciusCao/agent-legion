import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchJobVideoDetail } from '../videoApi'
import type { VideoJobDetailResponse } from '../videoApi'
import type {
  InteractionNode,
  InteractionOption,
  VideoArtifacts,
} from '../types'
import { VideoPlayer } from './VideoPlayer'
import { TimelineStrip } from './TimelineStrip'
import { SubtitlePanel } from './SubtitlePanel'
import { NodePanel } from './NodePanel'
import { CollapsiblePanel } from './CollapsiblePanel'
import { parseTimeSeconds } from '../helpers'
import styles from './VideoContentPanel.module.css'

export interface VideoContentPanelProps {
  jobId: string
  refreshKey?: string
}

function toChapters(
  raw: { [key: string]: unknown }[] | undefined
): VideoArtifacts['chapters'] {
  return (raw || []).map((c) => ({
    id: c.id != null ? String(c.id) : undefined,
    start: Number(c.start ?? c.start_time ?? 0),
    end: c.end != null ? Number(c.end) : undefined,
    title: String(c.title ?? ''),
  }))
}

function toInteractions(
  raw: { [key: string]: unknown }[] | undefined
): VideoArtifacts['interactions'] {
  return (raw || []).map((n) => ({
    id: n.id != null ? String(n.id) : undefined,
    type: n.type != null ? String(n.type) : undefined,
    trigger_time:
      (n.trigger_time as number | string | undefined) ??
      (n.start as number | string | undefined) ??
      0,
    instruction: n.instruction != null ? String(n.instruction) : undefined,
    hint: n.hint != null ? String(n.hint) : undefined,
    reference_sentence:
      n.reference_sentence != null ? String(n.reference_sentence) : undefined,
    options: Array.isArray(n.options)
      ? (n.options as InteractionOption[])
      : undefined,
    answer: Array.isArray(n.answer) ? (n.answer as string[]) : undefined,
    grading_mode: n.grading_mode != null ? String(n.grading_mode) : undefined,
  }))
}

function getInteractionTriggerTime(node: InteractionNode): number {
  const value = node.trigger_time ?? 0
  return typeof value === 'string' ? parseTimeSeconds(value) : Number(value)
}

function buildArtifacts(data: VideoJobDetailResponse): VideoArtifacts {
  return {
    subtitles: data.artifacts.subtitles || [],
    chapters: toChapters(data.artifacts.chapters),
    interactions: toInteractions(data.artifacts.interactions),
    metadata: data.artifacts.metadata || null,
    review: data.artifacts.review || null,
    checklist: data.artifacts.checklist || null,
  }
}

export function VideoContentPanel({
  jobId,
  refreshKey,
}: VideoContentPanelProps) {
  const [data, setData] = useState<VideoJobDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [dismissedInteractionIndexes, setDismissedInteractionIndexes] =
    useState<Set<number>>(new Set())
  const [interactionSentence, setInteractionSentence] = useState<string[]>([])
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const prevActiveIndexRef = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      setLoading(true)
      setError(null)
      try {
        const result = await fetchJobVideoDetail(jobId)
        if (cancelled) return
        setData(result)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    run()
    return () => {
      cancelled = true
    }
  }, [jobId, refreshKey])

  const artifacts = useMemo(() => (data ? buildArtifacts(data) : null), [data])

  const playableUrl = useMemo(() => {
    if (!data) return ''
    return data.artifacts.video_url || data.input.source_url || ''
  }, [data])

  const hasChapters = Boolean(artifacts && artifacts.chapters.length > 0)
  const hasInteractions = Boolean(
    artifacts && artifacts.interactions.length > 0
  )
  const hasSubtitles = Boolean(artifacts && artifacts.subtitles.length > 0)

  const activeInteractionIndex = useMemo<number>(() => {
    if (!artifacts) return -1
    return artifacts.interactions.findIndex((node, index) => {
      if (dismissedInteractionIndexes.has(index)) return false
      return currentTime >= getInteractionTriggerTime(node)
    })
  }, [artifacts, currentTime, dismissedInteractionIndexes])

  const activeInteraction =
    activeInteractionIndex >= 0 && artifacts
      ? artifacts.interactions[activeInteractionIndex]
      : null

  // Pause the video as soon as the viewer reaches an unwatched interaction.
  useEffect(() => {
    if (
      activeInteractionIndex >= 0 &&
      activeInteractionIndex !== prevActiveIndexRef.current
    ) {
      videoRef.current?.pause()
    }
    prevActiveIndexRef.current =
      activeInteractionIndex >= 0 ? activeInteractionIndex : null
  }, [activeInteractionIndex])

  const syncDismissedInteractions = useCallback(
    (time: number) => {
      if (!artifacts) return
      setDismissedInteractionIndexes((prev) => {
        let changed = false
        const next = new Set(prev)
        for (const index of prev) {
          const node = artifacts.interactions[index]
          if (node && time < getInteractionTriggerTime(node)) {
            next.delete(index)
            changed = true
          }
        }
        return changed ? next : prev
      })
    },
    [artifacts]
  )

  const handleTimeUpdate = useCallback(
    (time: number) => {
      setCurrentTime(time)
      syncDismissedInteractions(time)
    },
    [syncDismissedInteractions]
  )

  const handleSeek = useCallback(
    (time: number) => {
      const player = videoRef.current
      if (player) {
        player.currentTime = time
      }
      setCurrentTime(time)
      syncDismissedInteractions(time)
    },
    [syncDismissedInteractions]
  )

  const handleInteractionContinue = useCallback(() => {
    if (activeInteractionIndex >= 0) {
      setDismissedInteractionIndexes(
        (prev) => new Set([...prev, activeInteractionIndex])
      )
    }
    videoRef.current?.play()
  }, [activeInteractionIndex])

  const handleInteractionReset = useCallback(() => {
    setInteractionSentence([])
  }, [])

  const handleInteractionWordClick = useCallback((word: string) => {
    setInteractionSentence((prev) => [...prev, word])
  }, [])

  if (loading) {
    return <p className={styles.loading}>加载视频内容中...</p>
  }

  if (error) {
    return <p className={styles.error}>{error}</p>
  }

  if (!data || !artifacts) {
    return <p className={styles.empty}>视频内容尚未生成</p>
  }

  return (
    <div className={styles.panel} data-testid="video-content-panel">
      <section className={styles.playerSection}>
        <VideoPlayer
          artifacts={artifacts}
          src={playableUrl}
          onTimeUpdate={handleTimeUpdate}
          videoRef={videoRef}
          interactionNode={activeInteraction}
          interactionSentence={interactionSentence}
          onInteractionWordClick={handleInteractionWordClick}
          onInteractionReset={handleInteractionReset}
          onInteractionContinue={handleInteractionContinue}
        />
      </section>

      {(hasChapters || hasInteractions) && (
        <section className={styles.timelineSection}>
          <TimelineStrip
            chapters={artifacts.chapters}
            interactions={artifacts.interactions}
            currentTime={currentTime}
            onSeek={handleSeek}
          />
        </section>
      )}

      {hasSubtitles && (
        <CollapsiblePanel title="字幕" count={artifacts.subtitles.length}>
          <SubtitlePanel
            currentTime={currentTime}
            onSeek={handleSeek}
            subtitles={artifacts.subtitles}
          />
        </CollapsiblePanel>
      )}

      {hasInteractions && (
        <CollapsiblePanel
          title="交互节点"
          count={artifacts.interactions.length}
        >
          <NodePanel
            onSeek={handleSeek}
            artifacts={artifacts}
            triggeredNodeIndexes={dismissedInteractionIndexes}
            replayInteraction={(index) => {
              setDismissedInteractionIndexes((prev) => {
                const next = new Set(prev)
                next.delete(index)
                return next
              })
              const node = artifacts.interactions[index]
              if (node) {
                handleSeek(getInteractionTriggerTime(node))
              }
            }}
          />
        </CollapsiblePanel>
      )}
    </div>
  )
}
