import { useState } from 'react'
import {
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { PHASE_LABELS } from '../labels'
import styles from './BatchRerunDialog.module.css'

export type RerunItem = {
  id: string
  name: string
  currentPhase?: string
  status?: string
}

type BatchRerunDialogProps = {
  open: boolean
  items: RerunItem[]
  phases?: string[]
  itemLabel?: string
  onConfirm: (itemIds: string[], selectedPhase: string) => void | Promise<void>
  onClose: () => void
}

function isRunnable(
  item: RerunItem,
  selectedPhase: string,
  phases: string[]
): boolean {
  if (phases.length === 0) return true
  if (selectedPhase === '__failed__') {
    return item.status === 'failed'
  }
  if (item.status === 'running') return false
  if (item.status === 'completed') return true
  const currentIdx = phases.indexOf(item.currentPhase ?? '')
  const phaseIdx = phases.indexOf(selectedPhase)
  if (currentIdx === -1 || phaseIdx === -1) return false
  return phaseIdx <= currentIdx
}

export function BatchRerunDialog({
  open,
  items,
  phases = [],
  itemLabel = '项',
  onConfirm,
  onClose,
}: BatchRerunDialogProps) {
  const [selectedPhase, setSelectedPhase] = useState('download')
  const [loading, setLoading] = useState(false)

  if (!open) return null

  const runnableCount = items.filter((item) =>
    isRunnable(item, selectedPhase, phases)
  ).length

  const handleConfirm = async () => {
    setLoading(true)
    try {
      await onConfirm(
        items.map((item) => item.id),
        selectedPhase
      )
    } finally {
      setLoading(false)
    }
    onClose()
  }

  const showPhaseGrid = phases.length > 0

  const phaseChip = (phase: string, label: string) => {
    const selected = selectedPhase === phase
    return (
      <Chip
        key={phase}
        label={label}
        onClick={() => setSelectedPhase(phase)}
        variant={selected ? 'filled' : 'outlined'}
        sx={
          selected
            ? {
                backgroundColor: '#000000',
                color: '#ffffff',
                '&:hover': { backgroundColor: '#333333' },
              }
            : {}
        }
      />
    )
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{
        sx: {
          minWidth: '520px',
          maxWidth: '760px',
          width: 'min(760px, 92vw)',
        },
      }}
    >
      <DialogTitle>选择重跑阶段</DialogTitle>
      <DialogContent>
        <div className={styles.content}>
          {showPhaseGrid && (
            <div className={styles.phaseGrid}>
              {phaseChip('__failed__', PHASE_LABELS['__failed__'])}
              {phases.map((phase) =>
                phaseChip(phase, PHASE_LABELS[phase] ?? phase)
              )}
            </div>
          )}
          <div className={styles.videoGrid}>
            {items.map((item) => {
              const runnable = isRunnable(item, selectedPhase, phases)
              return (
                <div
                  key={item.id}
                  className={`${styles.videoTile} ${runnable ? '' : styles.videoTileDisabled}`}
                >
                  <span className={styles.videoName}>{item.name}</span>
                  {!runnable && showPhaseGrid && (
                    <span className={styles.videoHint}>
                      {selectedPhase === '__failed__'
                        ? '未失败，跳过'
                        : `当前处于 ${PHASE_LABELS[item.currentPhase ?? ''] ?? item.currentPhase}，无法重跑`}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
          <div className={styles.summary}>
            已选择 {items.length} 个{itemLabel}，可重跑 {runnableCount} 个
          </div>
        </div>
      </DialogContent>
      <DialogActions>
        <Button variant="text" type="button" onClick={onClose}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleConfirm}
          disabled={runnableCount === 0 || loading}
        >
          重跑 {runnableCount} 个{itemLabel}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
