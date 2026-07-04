import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import styles from './BatchUpgradeDialog.module.css'

export type BatchUpgradeJobItem = {
  id: string
  name: string
  status: string
  isWorkflowOutdated: boolean
}

export type BatchUpgradeDialogProps = {
  open: boolean
  jobs: BatchUpgradeJobItem[]
  itemLabel?: string
  loading?: boolean
  onConfirm: (upgradableJobIds: string[]) => void | Promise<void>
  onClose: () => void
}

function isUpgradeable(job: BatchUpgradeJobItem): boolean {
  return job.isWorkflowOutdated && job.status !== 'running'
}

function skipReason(job: BatchUpgradeJobItem): string | null {
  if (job.status === 'running') return '运行中'
  if (!job.isWorkflowOutdated) return '已是最新版本'
  return null
}

export function BatchUpgradeDialog({
  open,
  jobs,
  itemLabel = '任务',
  loading = false,
  onConfirm,
  onClose,
}: BatchUpgradeDialogProps) {
  if (!open) return null

  const selectedCount = jobs.length
  const upgradableJobs = jobs.filter(isUpgradeable)
  const upgradableCount = upgradableJobs.length
  const upgradableJobIds = upgradableJobs.map((j) => j.id)
  const completedCount = jobs.filter((j) => j.status === 'completed').length

  const handleConfirm = async () => {
    await onConfirm(upgradableJobIds)
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
      <DialogTitle>确认升级 workflow</DialogTitle>
      <DialogContent>
        <div className={styles.content}>
          <div className={styles.jobList}>
            {jobs.map((job) => {
              const upgradable = isUpgradeable(job)
              const reason = skipReason(job)
              return (
                <div
                  key={job.id}
                  className={`${styles.jobRow} ${upgradable ? '' : styles.jobRowDisabled}`}
                >
                  <span className={styles.jobName}>{job.name}</span>
                  {reason && <span className={styles.jobHint}>{reason}</span>}
                </div>
              )
            })}
          </div>
          <div className={styles.summary}>
            已选择 {selectedCount} 个{itemLabel}，可升级 {upgradableCount}{' '}
            个；其中 {completedCount} 个已完成，升级后将清空产物。
          </div>
        </div>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={loading}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleConfirm}
          disabled={upgradableCount === 0 || loading}
        >
          {loading ? '升级中...' : `升级 ${upgradableCount} 个${itemLabel}`}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
