import { Button, DialogActions } from '@mui/material'
import styles from '../JobRerunDialog/JobRerunDialog.module.css'

export type JobAllMatchingRerunFooterProps = {
  /** Server-computed eligible count; undefined while loading or on error. */
  eligibleCount: number | undefined
  selectedNodeLabel: string | null
  loading: boolean
  count: number
  onClose: () => void
  onConfirm: () => void | Promise<void>
}

/** Summary line + actions for the allMatching rerun dialog. The summary shows
 * the server-computed 「将重跑 N 个任务」once the preview answers, and falls
 * back to the plain server-resolution note while loading or on error. */
export function JobAllMatchingRerunFooter({
  eligibleCount,
  selectedNodeLabel,
  loading,
  count,
  onClose,
  onConfirm,
}: JobAllMatchingRerunFooterProps) {
  const summaryText = (() => {
    if (eligibleCount != null) {
      const prefix = `将重跑 ${eligibleCount} 个任务`
      return selectedNodeLabel
        ? `${prefix}（重跑节点：${selectedNodeLabel}）`
        : prefix
    }
    return selectedNodeLabel
      ? `重跑节点：${selectedNodeLabel}（按筛选条件由服务端解析）`
      : '按失败类别重跑（按筛选条件由服务端解析）'
  })()

  return (
    <>
      <div className={styles.summary}>{summaryText}</div>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={loading}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={onConfirm}
          disabled={loading || count === 0 || eligibleCount === 0}
        >
          {eligibleCount != null ? `确认重跑（${eligibleCount}）` : '确认重跑'}
        </Button>
      </DialogActions>
    </>
  )
}
