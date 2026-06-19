import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { PHASE_LABELS } from '../labels'
import { canContinueTo, canRerunTo, getSharedPhases } from '../helpers'
import type { RunToMode, VideoItem } from '../types'
import styles from './RunToDialog.module.css'

type RunToDialogProps = {
  open: boolean
  videos: VideoItem[]
  onClose: () => void
  onConfirm: (payload: {
    targetPhase: string
    startPhase: string | null
  }) => void | Promise<void>
}

const MODE_LABELS: Record<RunToMode, string> = {
  continue: '继续运行到',
  rerun: '重跑并运行到',
}

function getDefaultTarget(phases: string[]) {
  return phases.includes('chapter_generate') ? 'chapter_generate' : phases[0]
}

function displayName(video: VideoItem) {
  return video.external_id || video.title || video.id
}

function ineligibleReason(video: VideoItem) {
  if (video.status === 'running') return '正在处理中'
  return `当前处于 ${PHASE_LABELS[video.current_phase] ?? video.current_phase}，无法运行`
}

export function RunToDialog({
  open,
  videos,
  onClose,
  onConfirm,
}: RunToDialogProps) {
  const phases = useMemo(
    () => getSharedPhases(videos.map((video) => video.content_type)),
    [videos]
  )
  const defaultTargetPhase = getDefaultTarget(phases)
  const [mode, setMode] = useState<RunToMode>('continue')
  const [targetPhase, setTargetPhase] = useState(defaultTargetPhase)
  const [startPhase, setStartPhase] = useState(phases[0])

  useEffect(() => {
    queueMicrotask(() => {
      if (!phases.includes(targetPhase)) {
        setTargetPhase(defaultTargetPhase)
      }
      if (!phases.includes(startPhase)) {
        setStartPhase(phases[0])
      }
    })
  }, [defaultTargetPhase, phases, startPhase, targetPhase])

  const isRerun = mode === 'rerun'
  const selectedStartPhase = startPhase ?? phases[0]
  const selectedTargetPhase = targetPhase ?? defaultTargetPhase
  const runnableVideos = videos.filter((video) =>
    isRerun
      ? canRerunTo(video, selectedStartPhase, selectedTargetPhase)
      : canContinueTo(video, selectedTargetPhase)
  )
  const runnableCount = runnableVideos.length
  const confirmLabel = isRerun
    ? `从${PHASE_LABELS[selectedStartPhase] ?? selectedStartPhase}重跑到${
        PHASE_LABELS[selectedTargetPhase] ?? selectedTargetPhase
      }`
    : `运行到${PHASE_LABELS[selectedTargetPhase] ?? selectedTargetPhase}`

  const handleConfirm = async () => {
    if (runnableCount === 0) return

    await onConfirm({
      targetPhase: selectedTargetPhase,
      startPhase: isRerun ? selectedStartPhase : null,
    })
    onClose()
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          minWidth: '520px',
          maxWidth: '760px',
          width: 'min(760px, 92vw)',
        },
      }}
    >
      <DialogTitle>运行到阶段</DialogTitle>
      <DialogContent>
        <div className={styles.content}>
          <div className={styles.section}>
            <div className={styles.modeGrid}>
              {(Object.keys(MODE_LABELS) as RunToMode[]).map((modeKey) => (
                <Chip
                  key={modeKey}
                  label={MODE_LABELS[modeKey]}
                  color={mode === modeKey ? 'primary' : 'default'}
                  onClick={() => setMode(modeKey)}
                  sx={{ width: '100%', justifyContent: 'center' }}
                />
              ))}
            </div>
          </div>

          {isRerun && (
            <div className={styles.section}>
              <span className={styles.sectionLabel}>起始阶段</span>
              <div className={styles.chipGrid}>
                {phases.map((phase) => (
                  <Chip
                    key={phase}
                    label={PHASE_LABELS[phase] ?? phase}
                    color={selectedStartPhase === phase ? 'primary' : 'default'}
                    onClick={() => setStartPhase(phase)}
                    sx={{ width: '100%', justifyContent: 'center' }}
                  />
                ))}
              </div>
            </div>
          )}

          <div className={styles.section}>
            <span className={styles.sectionLabel}>目标阶段</span>
            <div className={styles.chipGrid}>
              {phases.map((phase) => (
                <Chip
                  key={phase}
                  label={PHASE_LABELS[phase] ?? phase}
                  color={selectedTargetPhase === phase ? 'primary' : 'default'}
                  onClick={() => setTargetPhase(phase)}
                  sx={{ width: '100%', justifyContent: 'center' }}
                />
              ))}
            </div>
          </div>

          <div className={styles.videoGrid}>
            {videos.map((video) => {
              const runnable = isRerun
                ? canRerunTo(video, selectedStartPhase, selectedTargetPhase)
                : canContinueTo(video, selectedTargetPhase)
              return (
                <div
                  key={video.id}
                  className={`${styles.videoTile} ${runnable ? '' : styles.videoTileDisabled}`}
                >
                  <span className={styles.videoName}>{displayName(video)}</span>
                  {!runnable && (
                    <span className={styles.videoHint}>
                      {ineligibleReason(video)}
                    </span>
                  )}
                </div>
              )
            })}
          </div>

          <div className={styles.summary}>
            已选择 {videos.length} 个视频，可运行 {runnableCount} 个
          </div>
        </div>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} variant="text">
          取消
        </Button>
        <Button
          onClick={handleConfirm}
          variant="contained"
          disabled={runnableCount === 0}
        >
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
