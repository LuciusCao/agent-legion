import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { fetchJobVideoDetail } from '../videoApi'
import type { VideoJobDetailResponse } from '../videoApi'
import type {
  ContentType,
  InteractionOption,
  VideoArtifacts,
  VideoItem,
} from '../types'
import { VideoPlayer } from './VideoPlayer'
import { TimelineStrip } from './TimelineStrip'
import { SubtitlePanel } from './SubtitlePanel'
import { NodePanel } from './NodePanel'
import { MetadataPanel } from './MetadataPanel'
import { MaterialIcon } from './MaterialIcon'
import styles from './VideoContentPanel.module.css'

export interface VideoContentPanelProps {
  jobId: string
  refreshKey?: string
}

type DialogType = 'subtitles' | 'nodes' | 'metadata' | null

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

function buildVideoItem(data: VideoJobDetailResponse): VideoItem {
  return {
    id: data.input.legacy_video_id,
    title: data.input.title,
    source_url: data.input.source_url,
    content_type: data.input.content_type as ContentType,
    external_id: data.input.external_id,
    knowledge_code: '',
    question_id: '',
    source_uuid: data.input.source_uuid,
    status: 'completed',
    current_phase: '',
    error_message: '',
    storage_dir: data.artifacts.video_url ? 'job-video' : '',
    duration: 0,
    packed: false,
  }
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
  const [dialogOpen, setDialogOpen] = useState<DialogType>(null)
  const videoRef = useRef<HTMLVideoElement | null>(null)

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

  const video = useMemo(() => (data ? buildVideoItem(data) : null), [data])
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
  const hasMetadata = Boolean(artifacts && artifacts.metadata)

  const handleTimeUpdate = useCallback((time: number) => {
    setCurrentTime(time)
  }, [])

  const handleSeek = useCallback((time: number) => {
    const player = videoRef.current
    if (!player) return
    player.currentTime = time
    setCurrentTime(time)
  }, [])

  const closeDialog = useCallback(() => setDialogOpen(null), [])

  if (loading) {
    return <p className={styles.loading}>加载视频内容中...</p>
  }

  if (error) {
    return <p className={styles.error}>{error}</p>
  }

  if (!data || !video || !artifacts) {
    return <p className={styles.empty}>视频内容尚未生成</p>
  }

  return (
    <div className={styles.panel} data-testid="video-content-panel">
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>{video.title || '未命名视频'}</h2>
          <p className={styles.meta}>
            来源 ID: {video.external_id || '—'}
            {video.source_url && (
              <a
                href={video.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className={styles.sourceLink}
              >
                原始链接
              </a>
            )}
          </p>
        </div>
        <div className={styles.actions}>
          {hasSubtitles && (
            <Button
              size="small"
              variant="outlined"
              startIcon={<MaterialIcon name="subtitles" />}
              onClick={() => setDialogOpen('subtitles')}
            >
              字幕
            </Button>
          )}
          {hasInteractions && (
            <Button
              size="small"
              variant="outlined"
              startIcon={<MaterialIcon name="account_tree" />}
              onClick={() => setDialogOpen('nodes')}
            >
              交互节点
            </Button>
          )}
          {hasMetadata && (
            <Button
              size="small"
              variant="outlined"
              startIcon={<MaterialIcon name="data_object" />}
              onClick={() => setDialogOpen('metadata')}
            >
              元数据
            </Button>
          )}
        </div>
      </header>

      <section className={styles.playerSection}>
        <VideoPlayer
          video={video}
          artifacts={artifacts}
          src={playableUrl}
          onTimeUpdate={handleTimeUpdate}
          videoRef={videoRef}
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
        <section className={styles.summarySection}>
          <span className={styles.summaryLabel}>字幕</span>
          <span className={styles.summaryValue}>
            {artifacts.subtitles.length} 条
          </span>
        </section>
      )}
      {hasInteractions && (
        <section className={styles.summarySection}>
          <span className={styles.summaryLabel}>交互节点</span>
          <span className={styles.summaryValue}>
            {artifacts.interactions.length} 个
          </span>
        </section>
      )}

      <Dialog
        open={dialogOpen === 'subtitles'}
        onClose={closeDialog}
        PaperProps={{ sx: { maxWidth: 720, width: '90vw' } }}
      >
        <DialogTitle>字幕</DialogTitle>
        <DialogContent sx={{ maxHeight: '60vh', overflow: 'auto', py: 1 }}>
          <SubtitlePanel
            currentTime={currentTime}
            onSeek={handleSeek}
            subtitles={artifacts.subtitles}
          />
        </DialogContent>
        <DialogActions>
          <Button variant="text" onClick={closeDialog}>
            关闭
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={dialogOpen === 'nodes'}
        onClose={closeDialog}
        PaperProps={{ sx: { maxWidth: 760, width: '90vw' } }}
      >
        <DialogTitle>交互节点</DialogTitle>
        <DialogContent sx={{ maxHeight: '60vh', overflow: 'auto', py: 1 }}>
          <NodePanel
            onSeek={handleSeek}
            artifacts={artifacts}
            triggeredNodeIndexes={new Set()}
          />
        </DialogContent>
        <DialogActions>
          <Button variant="text" onClick={closeDialog}>
            关闭
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={dialogOpen === 'metadata'}
        onClose={closeDialog}
        PaperProps={{ sx: { maxWidth: 640, width: '90vw' } }}
      >
        <DialogTitle>元数据</DialogTitle>
        <DialogContent sx={{ maxHeight: '60vh', overflow: 'auto' }}>
          <MetadataPanel metadata={artifacts.metadata} />
        </DialogContent>
        <DialogActions>
          <Button variant="text" onClick={closeDialog}>
            关闭
          </Button>
        </DialogActions>
      </Dialog>
    </div>
  )
}
