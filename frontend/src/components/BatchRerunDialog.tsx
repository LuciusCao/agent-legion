import { useState } from 'react'
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

  return (
    <md-dialog
      open
      onClosed={onClose}
      style={
        {
          minWidth: '520px',
          maxWidth: '760px',
          width: 'min(760px, 92vw)',
          '--md-dialog-container-color': '#ffffff',
        } as React.CSSProperties
      }
    >
      <div slot="headline">选择重跑阶段</div>
      <div slot="content">
        <div className={styles.content}>
          {showPhaseGrid && (
            <div className={styles.phaseGrid}>
              <md-filter-chip
                label={PHASE_LABELS['__failed__']}
                selected={selectedPhase === '__failed__' || undefined}
                onClick={() => setSelectedPhase('__failed__')}
              />
              {phases.map((phase) => (
                <md-filter-chip
                  key={phase}
                  label={PHASE_LABELS[phase] ?? phase}
                  selected={selectedPhase === phase || undefined}
                  onClick={() => setSelectedPhase(phase)}
                />
              ))}
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
      </div>
      <div slot="actions">
        <md-text-button type="button" onClick={onClose}>
          取消
        </md-text-button>
        <md-filled-button
          onClick={handleConfirm}
          disabled={runnableCount === 0 || loading || undefined}
        >
          重跑 {runnableCount} 个{itemLabel}
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
